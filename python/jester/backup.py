"""Back up the archive.

`data/jester.db` IS the product: every scraped comment, every nugget, every
idea, and the fingerprint skip-list that stops the whole corpus being
re-ingested. It exists once, on one disk, and it is gitignored — so nothing in
the repo protects it and a disk failure loses everything the scrapers have ever
collected.

WHY NOT `cp`. SQLite is being written by scheduled runs every 30 minutes. A
plain file copy taken mid-write produces a file that opens fine and is
corrupt — the worst failure mode there is, because it is discovered at restore
time. `sqlite3.Connection.backup()` uses SQLite's own online backup API, which
takes a consistent snapshot of a live database without blocking writers.

WHY NOT just the database. The vectors live in Qdrant, and re-embedding 1,780
nuggets takes minutes but needs the embedding model to still exist and still
produce the same vectors. The database is the source of truth; vectors are
derived and can be rebuilt with `jester reembed --all`. So this backs up the
database and says so.
"""

import gzip
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

#: Keep this many backups. Enough to survive "the corruption started a few days
#: ago and nobody noticed", which a single rolling backup does not.
DEFAULT_KEEP = 14


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_db(db_path: str, out_dir: str = "", *, compress: bool = True) -> dict:
    """Take a consistent snapshot of the archive.

    Returns {ok, path, bytes, source_bytes, detail}. Never raises: a backup
    that fails must say so and let the caller decide, not take down whatever
    invoked it.
    """
    src = Path(db_path)
    if not src.is_file():
        return {"ok": False, "error": f"no database at {src}"}

    target_dir = Path(out_dir) if out_dir else src.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    plain = target_dir / f"{src.stem}-{_stamp()}.db"

    try:
        # SQLite's online backup API: consistent even while the scheduled
        # cycle is mid-write, which a filesystem copy is not.
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            dest = sqlite3.connect(str(plain))
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
    except Exception as exc:  # noqa: BLE001
        if plain.exists():
            plain.unlink()
        return {"ok": False, "error": f"backup failed: {exc}"}

    final = plain
    if compress:
        gz = plain.with_suffix(plain.suffix + ".gz")
        try:
            with open(plain, "rb") as fin, gzip.open(gz, "wb", compresslevel=6) as fout:
                shutil.copyfileobj(fin, fout)
            plain.unlink()
            final = gz
        except Exception as exc:  # noqa: BLE001
            # An uncompressed backup is still a backup. Keep it and say what
            # happened rather than losing the snapshot over a gzip failure.
            return {
                "ok": True,
                "path": str(plain),
                "bytes": plain.stat().st_size,
                "source_bytes": src.stat().st_size,
                "detail": f"kept uncompressed: {exc}",
            }

    return {
        "ok": True,
        "path": str(final),
        "bytes": final.stat().st_size,
        "source_bytes": src.stat().st_size,
        "detail": "vectors are NOT included; rebuild them with `jester reembed --all`",
    }


def prune_backups(out_dir: str, keep: int = DEFAULT_KEEP, stem: str = "") -> list:
    """Delete all but the newest `keep` backups. Returns what was removed."""
    d = Path(out_dir)
    if not d.is_dir() or keep < 1:
        return []
    pattern = f"{stem}-*" if stem else "*"
    files = sorted(
        (p for p in d.glob(pattern) if p.suffix in (".db", ".gz")),
        key=lambda p: p.name,
        reverse=True,
    )
    removed = []
    for p in files[keep:]:
        try:
            p.unlink()
            removed.append(str(p))
        except OSError:
            continue
    return removed


def verify_backup(path: str) -> dict:
    """Open a backup and check it is a readable archive, not just a file.

    A backup nobody has opened is a hope, not a backup. This is the difference
    between "the file exists" and "the file contains the archive" — and it is
    exactly the check that catches a mid-write filesystem copy.
    """
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"no backup at {p}"}

    tmp = None
    try:
        target = p
        if p.suffix == ".gz":
            tmp = p.with_suffix("")
            tmp = tmp.parent / (tmp.name + ".verify")
            with gzip.open(p, "rb") as fin, open(tmp, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            target = tmp
        db = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            counts = {}
            for table in ("nuggets", "ideas", "ingest_batch", "runs"):
                try:
                    counts[table] = db.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                except sqlite3.Error:
                    counts[table] = None
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"unreadable: {exc}"}
    finally:
        if tmp is not None and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    if integrity != "ok":
        return {"ok": False, "error": f"integrity_check said {integrity!r}"}
    return {"ok": True, "integrity": integrity, "counts": counts}
