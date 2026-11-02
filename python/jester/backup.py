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
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

#: Keep this many backups. Enough to survive "the corruption started a few days
#: ago and nobody noticed", which a single rolling backup does not.
DEFAULT_KEEP = 14


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_db(db_path: str, out_dir: str = "", *, compress: bool = True,
              stamp: str = "") -> dict:
    """Take a consistent snapshot of the archive.

    Returns {ok, path, bytes, source_bytes, detail}. Never raises: a backup
    that fails must say so and let the caller decide, not take down whatever
    invoked it.

    `stamp` is optional and exists so a SET of files can share one timestamp;
    every existing caller gets the current time exactly as before.
    """
    src = Path(db_path)
    if not src.is_file():
        return {"ok": False, "error": f"no database at {src}"}

    target_dir = Path(out_dir) if out_dir else src.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    plain = target_dir / f"{src.stem}-{stamp or _stamp()}.db"

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
        except OSError:
            continue
        removed.append(str(p))
        # The manifest describes exactly this file; leaving it behind would
        # accumulate one orphan per pruned backup.
        try:
            (p.parent / (p.name + ".manifest.json")).unlink()
        except OSError:
            pass
    return removed


def _set_stamp(name: str) -> str:
    """The timestamp that says which backup SET a file belongs to."""
    match = re.search(r"-(\d{8}T\d{6}Z)", name)
    return match.group(1) if match else ""


def prune_backup_sets(out_dir: str, keep: int = DEFAULT_KEEP) -> list:
    """Delete all but the newest `keep` backup SETS. Returns what was removed.

    A set is everything one `backup_set` wrote — the archive snapshot, its
    companions (journal, chat, the dotenv) and their manifests — because they
    share one timestamp. Pruning per file would keep the newest N of EACH kind,
    so a directory of sets would never shrink; pruning per stamp keeps the
    newest N moments, which is what "keep a fortnight" is supposed to mean.

    A file whose name carries no stamp is not ours and is left alone: this
    deletes only what it can attribute to a set.
    """
    d = Path(out_dir)
    if not d.is_dir() or keep < 1:
        return []
    sets = {}
    for path in d.iterdir():
        stamp = _set_stamp(path.name)
        if path.is_file() and stamp:
            sets.setdefault(stamp, []).append(path)
    removed = []
    for stamp in sorted(sets, reverse=True)[keep:]:
        for path in sorted(sets[stamp]):
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(str(path))
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


# ---------------------------------------------------------------------------
# A backup SET, and a restore that proves it
# ---------------------------------------------------------------------------

#: Tables `verify_backup` counts. Named, not discovered: these are the archive's
#: own, and "did the restore bring the nuggets back" is the question a restore
#: is asked. A table that does not exist counts as None rather than failing the
#: check, because a fresh archive has not created every one yet.
COUNTED_TABLES = ("nuggets", "ideas", "ingest_batch", "runs")

#: Why the live config is not in the backup. It is versioned and reviewed, and
#: rolling it back as a side effect of restoring data would unmake a decision
#: nobody meant to revisit.
CONFIG_LIVE_NOTE = (
    "not copied: config-live/ is reviewed, versioned config; restoring data "
    "must not silently roll model choice back. Copy it by hand if the owner asks."
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_counts(db: sqlite3.Connection) -> dict:
    counts = {}
    for table in COUNTED_TABLES:
        try:
            counts[table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            counts[table] = None
    return counts


def _manifest_path(backup: Path) -> Path:
    return backup.with_name(backup.name + ".manifest.json")


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_manifest(backup: Path) -> dict | None:
    path = _manifest_path(backup)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _expected_counts(manifest: dict | None, name: str) -> dict | None:
    """What the manifest says a given file should contain, if anything.

    None means "nothing to compare", and it is the answer for a companion whose
    entry counts none of `COUNTED_TABLES` — `journal.db` and `chat.db` have no
    nuggets, so their entry is `{nuggets: None, ...}`. Comparing two dicts of
    Nones matched, and reporting "counts match" for that is a promise about a
    check that never ran.
    """
    if not manifest:
        return None
    entry = (manifest.get("files") or {}).get(name) or {}
    counts = entry.get("counts")
    if counts is None and name == manifest.get("backup"):
        counts = manifest.get("counts")
    if not isinstance(counts, dict) or all(v is None for v in counts.values()):
        return None
    return counts


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def companion_paths(db_path: str | Path, env_root: str | Path | None = None) -> list:
    """The files beside the archive that a restore would otherwise forget.

    Every path here is DERIVED from a module that owns it — the journal's own
    DEFAULT_DB, the chat store's own path function, the env loader's own
    candidate list — rather than written out again in this file. A second
    hard-coded "data/journal.db" is how a backup silently keeps covering a
    path the app stopped using.
    """
    from . import env
    from .chat.store import chat_db_path
    from .journal import DEFAULT_DB as JOURNAL_DB

    base = Path(db_path).parent
    found = []
    journal = base / Path(JOURNAL_DB).name
    if journal.is_file():
        found.append(("journal", journal))
    chat = Path(chat_db_path(str(db_path)))
    if chat.is_file():
        found.append(("chat", chat))
    root = Path(env_root) if env_root else Path(env.REPO_ROOT)
    for name in env.CANDIDATES:
        candidate = root / name
        if candidate.is_file():
            found.append(("env", candidate))
            break
    return found


def backup_set(db_path: str, out_dir: str = "", *, compress: bool = True,
               also_to: str = "", env_root: str | Path | None = None,
               stamp: str = "") -> dict:
    """Back up the archive AND what sits beside it, with a manifest.

    `backup_db` still backs up one database and keeps its exact shape; this is
    the command's view. It returns the same keys (`ok`, `path`, `bytes`,
    `source_bytes`, `detail`) so existing callers keep working, plus
    `manifest`, `files`, `absent` and `also_to`.

    Companions are copied only when present, and an absent one is named rather
    than invented: a backup that claims to cover a file it never saw is worse
    than one that says which files it did not find. `config-live/` is recorded
    as not copied, deliberately — see `CONFIG_LIVE_NOTE`.

    No companion failure fails the archive snapshot: `data/jester.db` is the
    product, and losing the whole backup because a chat store was locked would
    trade the important thing for the peripheral one. Failures are reported.
    """
    from .journal import DEFAULT_DB as JOURNAL_DB

    src = Path(db_path)
    shared_stamp = stamp or _stamp()
    main = backup_db(db_path, out_dir, compress=compress, stamp=shared_stamp)
    if not main["ok"]:
        return main

    target = Path(main["path"]).parent
    check = verify_backup(main["path"])
    files = {
        Path(main["path"]).name: {
            "role": "archive", "kind": "sqlite",
            "bytes": main["bytes"], "sha256": _sha256_file(Path(main["path"])),
            "counts": check.get("counts"),
        }
    }
    failed = {}
    for role, companion in companion_paths(src, env_root):
        if role == "env":
            # The dotenv is a text file, and it holds secrets: copy the bytes,
            # record only the hash, and keep the copy owner-readable.
            name = f"dotenv-{shared_stamp}.env"
            dst = target / name
            try:
                shutil.copyfile(companion, dst)
                try:
                    os.chmod(dst, 0o600)
                except OSError:
                    pass
            except OSError as exc:  # noqa: BLE001
                failed[companion.name] = str(exc)
                continue
            files[name] = {"role": role, "kind": "text",
                           "bytes": dst.stat().st_size,
                           "sha256": _sha256_file(dst),
                           "source": companion.name}
            continue
        got = backup_db(str(companion), str(target), compress=compress,
                        stamp=shared_stamp)
        if not got["ok"]:
            failed[companion.name] = got.get("error", "backup failed")
            continue
        name = Path(got["path"]).name
        verified = verify_backup(got["path"])
        files[name] = {"role": role, "kind": "sqlite",
                       "bytes": got["bytes"],
                       "sha256": _sha256_file(Path(got["path"])),
                       "counts": verified.get("counts")}

    # Name what was not there, so "covered when present" is visible rather than
    # assumed. The chat store's name is fixed by `chat_db_path`; the dotenv is
    # one of the loader's candidates or it is missing.
    absent = []
    for role, name in (("journal", Path(JOURNAL_DB).name),
                       ("chat", "chat.db"), ("env", ".env")):
        if not any(entry["role"] == role for entry in files.values()):
            absent.append(name)

    manifest = {
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(src),
        "backup": Path(main["path"]).name,
        "counts": check.get("counts"),
        "files": files,
        "absent": absent,
        "failed": failed,
        "not_included": {
            "config-live/": CONFIG_LIVE_NOTE,
            "vectors": ("Qdrant vectors are derived; rebuild them with "
                        "`jester reembed --all`"),
        },
        "restore": f"jester backup restore <{Path(main['path']).name}> <new path>",
    }
    manifest_file = _manifest_path(Path(main["path"]))
    _write_json_atomic(manifest_file, manifest)

    copied = []
    if also_to:
        second = Path(also_to)
        second.mkdir(parents=True, exist_ok=True)
        for name in list(files) + [manifest_file.name]:
            shutil.copy2(target / name, second / name)
            copied.append(str(second / name))

    return {
        "ok": True,
        "path": main["path"],
        "bytes": main["bytes"],
        "source_bytes": main["source_bytes"],
        "detail": main["detail"],
        "manifest": str(manifest_file),
        "files": sorted(files),
        "absent": absent,
        "failed": failed,
        "also_to": str(Path(also_to)) if also_to else "",
        "copied_to_second": copied,
        "stamp": shared_stamp,
        "stem": src.stem,
    }


def restore_backup(backup_path: str, dest: str) -> dict:
    """Restore a backup to `dest` and prove it before calling it restored.

    New file only: an existing destination is refused rather than overwritten,
    because the common way to lose an archive twice is to "restore" over the
    one copy that still worked. The file is proven three times — its SHA-256
    against the manifest entry, SQLite's own `PRAGMA integrity_check`, then the
    table counts against the same manifest — and any failure removes what was
    written, so a refusal can never be mistaken for a restore that produced a
    file.

    The hash comes first, and before anything is written: counts are an
    aggregate, so a file edited in place can still hold the right number of
    rows, and the manifest hash is the only thing that says these are the bytes
    that were backed up.
    """
    src, dst = Path(backup_path), Path(dest)
    if not src.is_file():
        return {"ok": False, "error": f"no backup at {src}"}
    if dst.exists():
        return {"ok": False,
                "error": f"refusing to overwrite {dst}; choose a new path"}

    try:
        manifest = _read_manifest(src)
        entry = ((manifest or {}).get("files") or {}).get(src.name) or {}
        want_hash = entry.get("sha256")
        if want_hash and _sha256_file(src) != want_hash:
            return {"ok": False,
                    "error": f"{src.name} does not match its manifest hash; refusing "
                             "to restore it"}
    except OSError as exc:
        # A backup the process cannot open -- locked on Windows, a permission
        # bit on POSIX -- used to raise out of the restore: the caller reads
        # `ok`/`error`, and a traceback here is a crash in the one command
        # someone runs when the archive is already gone.
        return {"ok": False, "error": f"unreadable: {exc}"}

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # A parent that cannot be made is an error like any other: the caller
        # reads `ok`/`error`, and a traceback here is a crash in the one
        # command someone runs when the archive is already gone.
        return {"ok": False, "error": f"cannot write {dst}: {exc}"}
    try:
        opener = gzip.open if src.suffix == ".gz" else open
        with opener(src, "rb") as fin, open(dst, "xb") as fout:
            shutil.copyfileobj(fin, fout)
    except Exception as exc:  # noqa: BLE001
        _unlink_quietly(dst)
        return {"ok": False, "error": f"unreadable: {exc}"}

    try:
        db = sqlite3.connect(f"file:{dst.as_posix()}?mode=ro", uri=True)
        try:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            counts = _table_counts(db)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        _unlink_quietly(dst)
        return {"ok": False, "error": f"unreadable: {exc}"}
    if integrity != "ok":
        _unlink_quietly(dst)
        return {"ok": False, "error": f"integrity_check said {integrity!r}"}

    expected = _expected_counts(manifest, src.name)
    if expected is not None and counts != expected:
        _unlink_quietly(dst)
        return {"ok": False,
                "error": f"counts {counts} differ from the manifest {expected}; "
                         "the file was removed"}
    # `expected is None` is the only thing that decides this. "Counts match"
    # over two dicts of Nones matched nothing, and saying so claimed a check
    # that never ran.
    if expected is None:
        note = ("no manifest beside the backup; counts were not compared"
                if manifest is None else
                f"{_manifest_path(src).name} carries no comparable counts for "
                f"{src.name}; they were not compared")
    else:
        note = f"counts match {_manifest_path(src).name}"
    return {"ok": True, "path": str(dst), "integrity": integrity,
            "counts": counts, "expected": expected,
            "matches_manifest": None if expected is None else True,
            "note": note}


def restore_drill(backup_path: str = "", *, db_path: str = "",
                  out_dir: str = "") -> dict:
    """Restore the newest backup into a temp dir and report — a rehearsal.

    The owner still chooses the real second volume; this proves the file opens
    and the counts match WITHOUT writing anything near the archive. The temp
    directory is removed whatever happened, so a drill cannot slowly become a
    second backup directory nobody prunes.
    """
    started = time.monotonic()
    if not backup_path:
        where = (Path(out_dir) if out_dir
                 else Path(db_path or "data/jester.db").parent / "backups")
        stem = Path(db_path).stem if db_path else "jester"
        found = sorted(
            (p for p in where.glob(f"{stem}-*") if p.suffix in (".db", ".gz")),
            key=lambda p: p.name) if where.is_dir() else []
        if not found:
            return {"ok": False, "error": f"no backup to drill in {where}"}
        backup_path = str(found[-1])

    work = Path(tempfile.mkdtemp(prefix="jester-restore-drill-"))
    name = Path(backup_path).name
    name = name[:-3] if name.endswith(".gz") else name
    dest = work / name
    try:
        got = restore_backup(backup_path, str(dest))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    got.update({"backup": backup_path, "path": str(dest),
                "seconds": round(time.monotonic() - started, 3)})
    return got
