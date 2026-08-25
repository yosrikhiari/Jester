"""§37.29 Operator console API: every jester feature surfaced as JSON.

Pure methods (no HTTP) so tests drive the console without sockets.
Actions wrap CLI behaviour and translate SystemExit into {ok, code} payloads.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from jester.cli import (
    _vector_for,
    cmd_mark,
    cmd_migrate,
    cmd_resynth,
    cmd_retention,
    cmd_run,
    evaluate_doctor,
    evaluate_smoke,
)
from jester.config import (
    MODEL_ROLES,
    ConfigError,
    Thresholds,
    load_config,
    save_models,
    save_thresholds,
)
from jester.export import default_export_dir, export_csv
from jester.fetchers.cloak import load_scraper_config
from jester.llm import PROMPT_VERSIONS, RUBRIC_VERSION
from jester.quota import day_key
from jester.sources import (
    PLATFORM_KINDS,
    SUPPORTED_KINDS,
    SourceError,
    find as find_source,
    load_sources,
    parse_source,
    save_sources,
    slugify,
    unique_name,
)
from jester.store import (
    get_idea,
    get_runs,
    list_ideas,
    list_nuggets,
    mark_reembedded,
    open_db,
    pending_reembeds,
    requeue_failed,
)

_LIVE_CONFIG_DIR = "config-live"
_PLATFORMS = tuple(PLATFORM_KINDS)


class ConsoleAPI:
    def __init__(self, db_path: str, config_dir: str | None = None):
        self.repo_root = Path(__file__).resolve().parents[3]
        self.config_dir = config_dir or str(self.repo_root / "config")
        self.db_path = db_path if db_path == ":memory:" else os.path.abspath(db_path)
        self.db = open_db(self.db_path)

    # ---- helpers ----------------------------------------------------------

    def _cfg(self):
        return load_config(self.config_dir)

    @property
    def _sources_path(self) -> Path:
        return Path(self.config_dir) / "sources.yaml"

    @property
    def _thresholds_path(self) -> Path:
        return Path(self.config_dir) / "thresholds.yaml"

    def _mirror_paths(self, filename: str):
        """config-live differs from config only in model choice, so the shared
        facts (which sources to scrape, which thresholds to apply) are kept in
        step across both dirs.

        Only the repo's own config/ mirrors: a console pointed at some other
        directory (a test fixture, a second profile) writes exactly there.
        """
        primary = Path(self.config_dir) / filename
        paths = [primary]
        repo_config = self.repo_root / "config"
        if not _same_dir(Path(self.config_dir), repo_config):
            return paths
        live = self.repo_root / _LIVE_CONFIG_DIR / filename
        if live.exists() and not _same_dir(live, primary):
            paths.append(live)
        return paths

    def _counts(self):
        q = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "nuggets": q("SELECT COUNT(*) FROM nuggets"),
            "ideas": q("SELECT COUNT(*) FROM ideas"),
            "pending_batches": q("SELECT COUNT(*) FROM ingest_batch WHERE status='pending'"),
            "failed_batches": q("SELECT COUNT(*) FROM ingest_batch WHERE status='failed'"),
            "reembed_backlog": q("SELECT COUNT(*) FROM nuggets WHERE needs_reembed=1"),
            "unprocessed": q("SELECT COUNT(*) FROM nuggets WHERE synthesized_at IS NULL"),
            "runs_active": q("SELECT COUNT(*) FROM runs WHERE status='running'"),
        }

    @staticmethod
    def _rowdict(row):
        return {k: row[k] for k in row.keys()} if row is not None else None

    def _quota_today(self):
        key = day_key(datetime.now(timezone.utc))
        per_platform = {}
        total = 0
        for platform in _PLATFORMS:
            row = self.db.execute(
                "SELECT used FROM quota WHERE platform=? AND day_key=?",
                (platform, key),
            ).fetchone()
            used = row[0] if row else 0
            per_platform[platform] = used
            total += used
        return {"day_key": key, "used_total": total, "used_by_platform": per_platform}

    def _action_guard(self, fn, *a, **kw):
        try:
            fn(*a, **kw)
            return {"ok": True}
        except SystemExit as e:
            return {"ok": False, "code": e.code}
        except Exception as exc:  # noqa: BLE001 - surfaced to the console UI
            return {"ok": False, "error": str(exc)}

    # ---- reads ------------------------------------------------------------

    def overview(self):
        runs = get_runs(self.db, limit=1)
        srcs = load_sources(self._sources_path)
        return {
            "ok": True,
            "db": self.db_path,
            "config_dir": self.config_dir,
            "counts": self._counts(),
            "last_run": self._rowdict(runs[0]) if runs else None,
            "quota": self._quota_today(),
            "prompt_versions": PROMPT_VERSIONS,
            "rubric_version": RUBRIC_VERSION,
            "sources_summary": {
                "total": len(srcs),
                "enabled": sum(1 for s in srcs if s.enabled),
                "unsupported": sum(1 for s in srcs if s.enabled and not s.supported),
            },
        }

    def runs(self, limit=25):
        out = []
        for r in get_runs(self.db, limit=limit):
            d = self._rowdict(r)
            d["floor_flags_list"] = json.loads(d.pop("floor_flags") or "[]")
            d["prefilter_funnel_obj"] = json.loads(d.pop("prefilter_funnel") or "{}")
            d["top_near_misses_list"] = json.loads(d.pop("top_near_misses") or "[]")
            d["platform_counts_obj"] = json.loads(d.pop("platform_counts") or "{}")
            d["models"] = json.loads(d.pop("models_used") or "null")
            out.append(d)
        return {"ok": True, "runs": out}

    def ideas(self):
        rows = list_ideas(self.db)
        return {"ok": True, "ideas": [self._rowdict(r) for r in rows]}

    def idea(self, idea_id):
        row = get_idea(self.db, int(idea_id))
        if row is None:
            return {"ok": False, "error": f"idea #{idea_id} not found"}
        d = self._rowdict(row)
        keys = json.loads(d.get("supporting_nuggets") or "[]")
        cites = []
        for k in keys:
            n = self.db.execute(
                "SELECT unique_key, category, extracted_insight, source_url "
                "FROM nuggets WHERE unique_key=?",
                (k,),
            ).fetchone()
            cites.append({
                "key": k,
                "found": n is not None,
                "detail": dict(n) if n else None,
            })
        d["citations"] = cites
        return {"ok": True, "idea": d}

    def nuggets(self, limit=500):
        rows = list_nuggets(self.db)
        return {"ok": True, "nuggets": [self._rowdict(r) for r in rows][:limit]}

    def doctor(self):
        findings = evaluate_doctor(self.db)
        return {"ok": len(findings) == 0, "findings": findings}

    def eval_gate(self):
        """Run the M2.2 calibration gate over known_good/bad sets."""
        from jester.cli import _parse_eval_line
        from jester.scoring import compute_overall

        cfg = self._cfg()
        gate = cfg.thresholds.good_idea_min
        good_path = self.repo_root / "eval" / "known_good.md"
        bad_path = self.repo_root / "eval" / "known_bad.md"
        checks = failures = 0
        details = []
        for path, expect_pass in ((good_path, True), (bad_path, False)):
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                parsed = _parse_eval_line(line)
                if not parsed:
                    continue
                checks += 1
                title, demand, feasibility, competition = parsed
                overall = compute_overall(demand, feasibility, competition)
                ok = (overall >= gate) == expect_pass
                if not ok:
                    failures += 1
                details.append({"title": title, "overall": overall,
                                "expect_pass": expect_pass, "ok": ok})
        return {"ok": failures == 0, "checks": checks, "failures": failures,
                "gate": gate, "details": details}

    # ---- sources ----------------------------------------------------------

    def sources(self):
        """The curated ingest list, plus the vocabulary the UI needs to render
        an add-form without hard-coding platform knowledge in JavaScript."""
        srcs = load_sources(self._sources_path)
        return {
            "ok": True,
            "path": str(self._sources_path),
            "sources": [s.to_dict() for s in srcs],
            "platform_kinds": {k: list(v) for k, v in PLATFORM_KINDS.items()},
            "supported_kinds": [list(pair) for pair in sorted(SUPPORTED_KINDS)],
        }

    def _write_sources(self, srcs):
        written = []
        for path in self._mirror_paths("sources.yaml"):
            save_sources(path, srcs)
            written.append(str(path))
        return written

    def add_source(self, url, platform="auto", name=None, enabled=True, notes=""):
        try:
            src = parse_source(url, platform, name=name, enabled=enabled, notes=notes)
        except SourceError as exc:
            return {"ok": False, "error": str(exc)}
        srcs = load_sources(self._sources_path)
        if any(s.url.rstrip("/").lower() == src.url.rstrip("/").lower() for s in srcs):
            return {"ok": False, "error": f"{src.url} is already in the list"}
        src.name = unique_name(srcs, src.name)
        srcs.append(src)
        written = self._write_sources(srcs)
        return {"ok": True, "source": src.to_dict(), "written_to": written}

    def update_source(self, name, **fields):
        srcs = load_sources(self._sources_path)
        try:
            src = find_source(srcs, name)
        except SourceError as exc:
            return {"ok": False, "error": str(exc)}
        if "url" in fields and fields["url"]:
            try:
                reparsed = parse_source(fields["url"], fields.get("platform") or "auto")
            except SourceError as exc:
                return {"ok": False, "error": str(exc)}
            src.url, src.platform, src.kind = reparsed.url, reparsed.platform, reparsed.kind
        if "enabled" in fields:
            src.enabled = bool(fields["enabled"])
        if "notes" in fields:
            src.notes = str(fields["notes"] or "").strip()
        if fields.get("new_name"):
            src.name = unique_name(srcs, slugify(fields["new_name"]), skip=name)
        written = self._write_sources(srcs)
        return {"ok": True, "source": src.to_dict(), "written_to": written}

    def delete_source(self, name):
        srcs = load_sources(self._sources_path)
        remaining = [s for s in srcs if s.name != name]
        if len(remaining) == len(srcs):
            return {"ok": False, "error": f"no source named {name!r}"}
        written = self._write_sources(remaining)
        return {"ok": True, "removed": name, "written_to": written}

    # ---- config -----------------------------------------------------------

    def config(self):
        t = self._cfg().thresholds
        scraper = load_scraper_config(Path(self.config_dir) / "scraper.yaml").__dict__
        return {
            "ok": True,
            "config_dir": self.config_dir,
            "thresholds": {
                "llm_provider": t.llm_provider,
                "embedding_provider": t.embedding_provider,
                "extractor_model": t.extractor_model,
                "archivist_model": t.archivist_model,
                "synthesizer_model": t.synthesizer_model,
                "critic_model": t.critic_model,
                "embedding_model": t.embedding_model,
                "dedup_threshold": t.dedup_threshold,
                "good_idea_min": t.good_idea_min,
                "request_delay_ms": t.request_delay_ms,
                "max_comments_per_thread": t.max_comments_per_thread,
                "max_threads_per_source": t.max_threads_per_source,
                "min_comments_per_thread": t.min_comments_per_thread,
                "min_upvotes": t.min_upvotes,
                "prefilter": {
                    "min_chars": t.prefilter_min_chars,
                    "max_chars": t.prefilter_max_chars,
                    "max_emoji": t.prefilter_max_emoji,
                    "max_mentions": t.prefilter_max_mentions,
                    "min_words": t.prefilter_min_words,
                },
                "competitor_ttl_days": t.competitor_ttl_days,
                "critic_web_rate_limit": t.critic_web_rate_limit,
                "critic_web_daily_budget": t.critic_web_daily_budget,
            },
            "editable": {
                key: {"type": caster.__name__, "range": Thresholds._RANGES.get(key),
                      "value": getattr(t, key)}
                for key, caster in Thresholds._EDITABLE.items()
            },
            "scraper": scraper,
            "sources": [s.to_dict() for s in load_sources(self._sources_path)],
            "env": {
                "JESTER_QDRANT_URL": os.environ.get("JESTER_QDRANT_URL", ""),
                "JESTER_ORIGIN": os.environ.get("JESTER_ORIGIN", "manual"),
                "JESTER_GO_WORKER": os.environ.get("JESTER_GO_WORKER", ""),
                "JESTER_WORKER_LIVE": os.environ.get("JESTER_WORKER_LIVE", ""),
                "JESTER_NOTIFY_CMD_set": bool(os.environ.get("JESTER_NOTIFY_CMD")),
            },
        }

    # ---- agent models -----------------------------------------------------

    def _profiles(self):
        """Config directories the console may edit. Model choice is what
        separates them, so each is edited on its own — never mirrored.

        The repo's config/config-live pair is offered only when the console is
        actually serving it; pointed anywhere else, the configured dir is the
        one and only profile.
        """
        active = Path(self.config_dir)
        candidates = [active]
        if _same_dir(active, self.repo_root / "config"):
            candidates.append(self.repo_root / _LIVE_CONFIG_DIR)
        out, seen = [], set()
        for path in candidates:
            if not (path / "thresholds.yaml").exists():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
        return out

    def _resolve_profile(self, profile):
        """Map a profile name from the UI onto a config dir, refusing anything
        that is not one of the known profiles."""
        if not profile:
            return Path(self.config_dir)
        for path in self._profiles():
            if profile in (str(path), path.name, str(path.resolve())):
                return path
        raise ConfigError(f"unknown config profile {profile!r}")

    def ollama_models(self):
        """Names Ollama currently has pulled, or an empty list when it is down.
        Never fabricated: an empty list means "could not ask", not "none"."""
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
                payload = json.loads(r.read())
        except Exception:  # noqa: BLE001 - daemon down is a normal state here
            return None
        names = [m.get("name") for m in payload.get("models", []) if m.get("name")]
        return sorted(set(names))

    def models(self, profile=None):
        try:
            path = self._resolve_profile(profile)
        except ConfigError as exc:
            return {"ok": False, "error": str(exc)}
        t = load_config(str(path)).thresholds
        installed = self.ollama_models()
        current = {
            "extractor": t.extractor_model,
            "archivist": t.archivist_model,
            "synthesizer": t.synthesizer_model,
            "critic": t.critic_model,
            "embedding_model": t.embedding_model,
        }
        return {
            "ok": True,
            "profile": path.name,
            "profile_dir": str(path),
            "profiles": [
                {"name": p.name, "dir": str(p),
                 "llm_provider": load_config(str(p)).thresholds.llm_provider}
                for p in self._profiles()
            ],
            "roles": list(MODEL_ROLES) + ["embedding_model"],
            "models": current,
            "ollama_up": installed is not None,
            "installed": installed or [],
            # Anything configured but not pulled would fail at run time — the
            # UI marks these rather than letting the run discover it. Ollama
            # reports `name:latest`, so a bare `name` is the same model.
            "missing": sorted({
                name for name in current.values()
                if name and not _model_installed(name, installed)
            }) if installed is not None else [],
        }

    def update_models(self, patch, profile=None):
        try:
            path = self._resolve_profile(profile)
            applied = save_models(path / "thresholds.yaml", patch)
        except (ConfigError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "applied": applied, "profile": path.name,
                "written_to": str(path / "thresholds.yaml")}

    def update_thresholds(self, patch):
        try:
            written = []
            applied = {}
            for path in self._mirror_paths("thresholds.yaml"):
                applied = save_thresholds(path, patch)
                written.append(str(path))
        except (ConfigError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "applied": applied, "written_to": written}

    @staticmethod
    def _go_binary():
        """Resolve the Go toolchain: GO_BIN, then PATH, then the vendored
        Windows install. PATH used to be skipped, so a perfectly normal
        `go` install reported the worker as unavailable."""
        explicit = os.environ.get("GO_BIN")
        if explicit and Path(explicit).exists():
            return explicit
        found = shutil.which("go")
        if found:
            return found
        vendored = (Path(os.environ.get("LOCALAPPDATA", ""))
                    / "Programs" / "go-toolchain" / "bin" / "go.exe")
        return str(vendored) if vendored.exists() else None

    def infra(self):
        def tcp(host, port, timeout=1.5):
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except OSError:
                return False

        installed = self.ollama_models()
        ollama_up = installed is not None
        ollama_models = installed or []

        cloak_up = tcp("127.0.0.1", 9222)
        go_bin = self._go_binary()
        return {
            "ok": True,
            "ollama": {"up": ollama_up, "models": ollama_models},
            "qdrant_6333": tcp("127.0.0.1", 6333),
            "cloakserve_9222": cloak_up,
            "worker_go_available": bool(go_bin),
            "go_bin": go_bin or "",
            # Live ingestion needs both halves; the UI gates its button on this.
            "can_ingest_live": bool(go_bin) and cloak_up,
        }

    # ---- actions ----------------------------------------------------------

    def run_pipeline(self, live_models=False, run_id="console-run"):
        """Full pipeline over pending batches. With no pending batches the
        FixtureFetcher seeds the deterministic sample (mock corpus).

        Live *ingestion* is the Go worker's job (`worker -live`); this runs the
        Python agent pipeline over whatever is already queued.
        """
        args = type("Args", (), {})()
        args.db = self.db_path
        args.run = run_id
        args.config = (
            str(self.repo_root / _LIVE_CONFIG_DIR)
            if live_models
            else self.config_dir
        )
        old_origin = os.environ.get("JESTER_ORIGIN")
        os.environ["JESTER_ORIGIN"] = "manual"
        try:
            result = self._action_guard(cmd_run, args)
        finally:
            if old_origin is None:
                os.environ.pop("JESTER_ORIGIN", None)
            else:
                os.environ["JESTER_ORIGIN"] = old_origin
        result["overview"] = self.overview()["counts"]
        return result

    def ingest(self, live=False, run_id="console-ingest", timeout=900):
        """Run the Go ingestion worker over the curated source list.

        This is the missing half of the loop: adding a subreddit in the Sources
        tab does nothing until something fetches it, and `run_pipeline` only
        processes what is already queued.
        """
        go_bin = self._go_binary()
        if not go_bin:
            return {"ok": False, "error": "no Go toolchain found — install Go or set GO_BIN"}
        cmd = [
            go_bin, "run", "./cmd/worker",
            "-config", os.path.abspath(self.config_dir),
            "-db", self.db_path,
            "-run", run_id,
        ]
        if live:
            cmd.append("-live")
        env = os.environ.copy()
        if not live:
            env["JESTER_MOCK"] = "1"
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                cmd, cwd=str(self.repo_root / "go"), env=env,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"worker exceeded {timeout}s and was killed"}
        except OSError as exc:
            return {"ok": False, "error": f"could not start the worker: {exc}"}
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "live": bool(live),
            "output": out[-4000:],
            "error": err[-2000:] if proc.returncode != 0 else "",
            "queued_new_comments": _queued_from_output(out),
            "counts": self._counts(),
        }

    def export(self, out_dir=None):
        """CSV dump of the archive, split by content type and origin."""
        try:
            target = out_dir or default_export_dir(self.db_path)
            manifest = export_csv(self.db, target)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "out_dir": str(target), **manifest}

    def smoke(self):
        findings = evaluate_smoke(open_db(self.db_path))
        return {"ok": not findings, "findings": findings}

    def mark_idea(self, idea_id, status):
        return self._action_guard(cmd_mark, _ns(db=self.db_path, id=int(idea_id), status=status))

    def requeue(self):
        n = requeue_failed(self.db)
        return {"ok": True, "requeued": n}

    def reembed(self):
        cfg = self._cfg()
        vector = _vector_for(cfg, self.db_path)
        fixed = failed = 0
        last_err = None
        for r in pending_reembeds(self.db):
            key = r["unique_key"]
            try:
                vector.upsert(key, r["raw_text"] or "", {})
                mark_reembedded(self.db, key, f"emb:{abs(hash(key)) % (2 ** 63)}")
                fixed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                last_err = str(exc)
        return {"ok": failed == 0, "fixed": fixed, "failed": failed,
                "last_error": last_err}

    def resynth(self, idea_id):
        return self._action_guard(
            cmd_resynth, _ns(db=self.db_path, config=self.config_dir, id=int(idea_id))
        )

    def migrate(self):
        return self._action_guard(cmd_migrate, _ns(db=self.db_path))

    def retention(self):
        return self._action_guard(cmd_retention, _ns(db=self.db_path))

    def seed_mock(self):
        """Queue the deterministic fixture so a pipeline run has input."""
        from jester.fetchers import FixtureFetcher
        from jester.store import enqueue_new_only

        queued = 0
        for b in FixtureFetcher().fetch():
            queued += enqueue_new_only(
                self.db, b["platform"], b["source"], b["thread_id"], b["comments"],
                run_id="console-seed",
            )
        return {"ok": True, "queued_new_comments": queued}


def _model_installed(name: str, installed) -> bool:
    """`nomic-embed-text` and `nomic-embed-text:latest` are the same pull."""
    if not installed:
        return False
    wanted = name if ":" in name else f"{name}:latest"
    return any(have in (name, wanted) for have in installed)


_QUEUED_RE = re.compile(r"^\[live\] done: (\d+) comment", re.M)
_MOCK_QUEUED_RE = re.compile(r"^enqueued batch id=\d+ with (\d+) kept comments", re.M)


def _queued_from_output(text: str):
    """Pull the worker's own tally out of its log so the console reports what
    the worker actually did, rather than guessing from a DB delta."""
    for pattern in (_QUEUED_RE, _MOCK_QUEUED_RE):
        m = pattern.search(text or "")
        if m:
            return int(m.group(1))
    return None


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:  # a path that cannot be resolved is not the repo's own
        return False


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns
