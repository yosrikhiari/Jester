"""Rollback journal vs WAL, measured before changing the default (S-11).

`open_db` defaults to SQLite's rollback journal because the archive can live on
a Windows bind-mounted Docker volume, where WAL's `-wal`/`-shm` sidecars are
not reliably arbitrated. That is a claim, and this harness exists to keep it
from being a guess forever.

It writes a throwaway database (never `data/jester.db`), runs one writer and N
readers in both modes, and reports commits, query throughput, lock errors and
tail latency. `recovery_check` reopens the file to prove the mode survives a
restart and the rows are intact.

Run it:

    python -m jester.wal_bench --rows 50000 --seconds 3

The decision and the numbers it produced are in
`docs/reports/2026-10-31-wal-benchmark.md`.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from jester.store import open_db

MODES = ("delete", "wal")

#: Anchored to the repository, NOT the working directory. The first guard
#: compared `Path("data/jester.db").resolve()`, which is whatever data/ sits
#: under the shell's current directory — so running `--db ../data/jester.db`
#: from python/ pointed the comparison at python/data/jester.db and seed()
#: deleted the real archive's rows.
LIVE_ARCHIVE = Path(__file__).resolve().parents[2] / "data" / "jester.db"


def check_not_live(path) -> None:
    """Refuse the live archive, wherever the command was typed from."""
    if Path(path).resolve() == LIVE_ARCHIVE.resolve():
        raise SystemExit("refusing to benchmark against the live archive")


def _percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


def connect(path, mode, *, set_mode=True):
    """One connection in `mode`, with the same busy timeout the app uses.

    `set_mode=False` is for the worker connections: switching journal mode
    takes an exclusive lock, and two threads asking for WAL at the same
    instant make the loser fail with "database is locked" before any busy
    timeout applies. `benchmark` switches once, up front, which is also how
    production works — `open_db` sets the mode, then the process reuses it.
    """
    db = sqlite3.connect(str(path), timeout=5.0)
    db.execute("PRAGMA busy_timeout=5000")
    if set_mode:
        db.execute(f"PRAGMA journal_mode={mode}")
    db.execute("PRAGMA synchronous=NORMAL")
    return db


def seed(path, rows=20000):
    """A database with enough rows that a read is not just a page cache hit."""
    db = open_db(str(path))
    db.execute("DELETE FROM nuggets")
    db.executemany(
        "INSERT INTO nuggets (unique_key, platform, raw_text, extracted_insight)"
        " VALUES (?, 'bench', ?, ?)",
        ((f"seed-{i}", f"comment body {i} " * 8, f"insight {i}") for i in range(rows)))
    db.commit()
    db.close()
    return rows


def benchmark(path, mode, *, seconds=2.0, readers=2, batch=50):
    """One writer and `readers` readers against one database."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    connect(path, mode).close()
    stop = threading.Event()
    lock = threading.Lock()
    out = {"mode": mode, "writes": 0, "write_errors": 0, "write_lat": [],
           "reads": 0, "read_errors": 0, "read_lat": []}

    def write_loop():
        # Connections are created IN their thread: sqlite3 objects are bound
        # to the thread that made them, and this is also what each process
        # in production does.
        writer = connect(path, mode, set_mode=False)
        index = 0
        while not stop.is_set():
            started = time.perf_counter()
            try:
                writer.executemany(
                    "INSERT INTO nuggets (unique_key, platform, raw_text,"
                    " extracted_insight) VALUES (?, 'bench-live', ?, ?)",
                    ((f"live-{mode}-{index + i}", "body " * 20, f"live {index + i}")
                     for i in range(batch)))
                writer.commit()
                elapsed = (time.perf_counter() - started) * 1000.0
                with lock:
                    out["writes"] += 1
                    out["write_lat"].append(elapsed)
            except sqlite3.OperationalError:
                with lock:
                    out["write_errors"] += 1
            index += batch
        writer.close()

    def read_loop():
        reader = connect(path, mode, set_mode=False)
        while not stop.is_set():
            started = time.perf_counter()
            try:
                reader.execute("SELECT COUNT(*) FROM nuggets").fetchone()
                reader.execute(
                    "SELECT id, extracted_insight FROM nuggets"
                    " ORDER BY id DESC LIMIT 50").fetchall()
                elapsed = (time.perf_counter() - started) * 1000.0
                with lock:
                    out["reads"] += 1
                    out["read_lat"].append(elapsed)
            except sqlite3.OperationalError:
                with lock:
                    out["read_errors"] += 1
        reader.close()

    threads = [threading.Thread(target=write_loop, daemon=True)]
    threads += [threading.Thread(target=read_loop, daemon=True) for _ in range(readers)]
    for thread in threads:
        thread.start()
    time.sleep(seconds)
    stop.set()
    for thread in threads:
        thread.join(timeout=10)

    return {
        "mode": mode,
        "writes": out["writes"],
        "rows_written": out["writes"] * batch,
        "write_errors": out["write_errors"],
        "write_p95_ms": _percentile(out["write_lat"], 95),
        "reads": out["reads"],
        "read_errors": out["read_errors"],
        "read_p50_ms": _percentile(out["read_lat"], 50),
        "read_p95_ms": _percentile(out["read_lat"], 95),
    }


def recovery_check(path, mode):
    """Write, close, reopen: is the mode kept and the row still there?"""
    marker = f"recovery-{mode}"
    db = connect(path, mode)
    db.execute(
        "INSERT INTO nuggets (unique_key, platform, raw_text, extracted_insight)"
        " VALUES (?, 'recovery', 'body', 'insight')", (marker,))
    db.commit()
    db.close()

    again = sqlite3.connect(str(path))
    integrity = again.execute("PRAGMA integrity_check").fetchone()[0]
    mode_now = again.execute("PRAGMA journal_mode").fetchone()[0].lower()
    row = again.execute(
        "SELECT 1 FROM nuggets WHERE unique_key=?", (marker,)).fetchone()
    again.close()
    return {"mode": mode, "mode_persisted": mode_now == mode,
            "row_present": row is not None, "integrity": integrity}


def format_markdown(results) -> str:
    head = ("| mode | commits | rows written | write errors | write p95 (ms) |"
            " reads | read errors | read p50 (ms) | read p95 (ms) |\n"
            "|---|---|---|---|---|---|---|---|---|\n")
    body = ""
    for r in results:
        body += (
            f"| {r['mode']} | {r['writes']} | {r['rows_written']} |"
            f" {r['write_errors']} | {r['write_p95_ms']:.1f} |"
            f" {r['reads']} | {r['read_errors']} |"
            f" {r['read_p50_ms']:.2f} | {r['read_p95_ms']:.2f} |\n")
    return head + body


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None,
                        help="throwaway database path (default: a new temp dir)")
    parser.add_argument("--rows", type=int, default=50000)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--readers", type=int, default=2)
    args = parser.parse_args(argv)

    if args.db:
        base = Path(args.db)
        base.parent.mkdir(parents=True, exist_ok=True)
    else:
        import tempfile

        base = Path(tempfile.mkdtemp(prefix="jester-wal-")) / "bench.db"
    check_not_live(base)

    seed(base, args.rows)
    results = [benchmark(base, mode, seconds=args.seconds, readers=args.readers)
               for mode in MODES]
    print(f"database: {base} ({args.rows} seeded rows, {args.seconds}s per mode,"
          f" {args.readers} reader(s))")
    print(format_markdown(results))
    for mode in MODES:
        print(f"recovery {mode}: {recovery_check(base, mode)}")
    return results


if __name__ == "__main__":
    main()
