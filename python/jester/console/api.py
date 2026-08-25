"""§37.29 Operator console API: every jester feature surfaced as JSON.

Pure methods (no HTTP) so tests drive the console without sockets.
Actions wrap CLI behaviour and translate SystemExit into {ok, code} payloads.
"""
import json
import os
import socket
import urllib.request
from pathlib import Path

from jester.agents.synthesizer import Synthesizer
from jester.cli import (
    evaluate_doctor,
    evaluate_smoke,
    cmd_mark,
    cmd_migrate,
    cmd_resynth,
    cmd_retention,
    cmd_reembed,
    cmd_run,
)
from jester.config import load_config, load_sources
from jester.embed import select_embedding
from jester.fetchers.cloak import load_scraper_config
from jester.llm import PROMPT_VERSIONS, RUBRIC_VERSION
from jester.store import (
    get_idea,
    get_runs,
    list_ideas,
    list_nuggets,
    mark_reembedded,
    open_db,
    pending_batches,
    pending_reembeds,
    requeue_failed,
)

_LIVE_CONFIG_DIR = "config-live"


class ConsoleAPI:
    def __init__(self, db_path: str, config_dir: str | None = None):
        self.repo_root = Path(__file__).resolve().parents[3]
        self.config_dir = config_dir or str(self.repo_root / "config")
        self.db_path = db_path if db_path == ":memory:" else os.path.abspath(db_path)
        self.db = open_db(self.db_path)

    # ---- helpers ----------------------------------------------------------

    def _cfg(self):
        return load_config(self.config_dir)

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
        from datetime import datetime, timezone

        from jester.quota import day_key

        key = day_key(datetime.now(timezone.utc))
        per_platform = {}
        total = 0
        for platform in ("reddit", "youtube", "tiktok"):
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
        return {
            "ok": True,
            "db": self.db_path,
            "counts": self._counts(),
            "last_run": self._rowdict(runs[0]) if runs else None,
            "quota": self._quota_today(),
            "prompt_versions": PROMPT_VERSIONS,
            "rubric_version": RUBRIC_VERSION,
        }

    def runs(self, limit=25):
        out = []
        for r in get_runs(self.db, limit=limit):
            d = self._rowdict(r)
            d["floor_flags_list"] = json.loads(d.pop("floor_flags") or "[]")
            d["prefilter_funnel_obj"] = json.loads(d.pop("prefilter_funnel") or "{}")
            d["top_near_misses_list"] = json.loads(d.pop("top_near_misses") or "[]")
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

    def nuggets(self, limit=100):
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
                details.append({"title": title, "overall": overall, "expect_pass": expect_pass, "ok": ok})
        return {"ok": failures == 0, "checks": checks, "failures": failures, "gate": gate, "details": details}

    def config(self):
        t = self._cfg().thresholds
        scraper = load_scraper_config(Path(self.config_dir) / "scraper.yaml").__dict__
        sources = [
            s.__dict__ for s in load_sources(Path(self.config_dir) / "sources.yaml")
        ]
        return {
            "ok": True,
            "thresholds": {
                "llm_provider": t.llm_provider,
                "embedding_provider": t.embedding_provider,
                "extractor_model": t.extractor_model,
                "synthesizer_model": t.synthesizer_model,
                "critic_model": t.critic_model,
                "embedding_model": t.embedding_model,
                "dedup_threshold": t.dedup_threshold,
                "good_idea_min": t.good_idea_min,
                "request_delay_ms": t.request_delay_ms,
                "max_comments_per_thread": t.max_comments_per_thread,
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
            "scraper": scraper,
            "sources": sources,
            "env": {
                "JESTER_QDRANT_URL": os.environ.get("JESTER_QDRANT_URL", ""),
                "JESTER_ORIGIN": os.environ.get("JESTER_ORIGIN", "manual"),
                "JESTER_GO_WORKER": os.environ.get("JESTER_GO_WORKER", ""),
                "JESTER_WORKER_LIVE": os.environ.get("JESTER_WORKER_LIVE", ""),
                "JESTER_NOTIFY_CMD_set": bool(os.environ.get("JESTER_NOTIFY_CMD")),
            },
        }

    def infra(self):
        def tcp(host, port, timeout=1.5):
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except OSError:
                return False

        ollama_models = []
        ollama_up = False
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
                ollama_models = [
                    m.get("name") for m in json.loads(r.read()).get("models", [])
                ]
                ollama_up = True
        except Exception:
            pass

        go_exe = Path(
            os.environ.get(
                "GO_BIN",
                str(Path(os.environ.get("LOCALAPPDATA", ""))
                    / "Programs" / "go-toolchain" / "bin" / "go.exe"),
            )
        )
        return {
            "ok": True,
            "ollama": {"up": ollama_up, "models": ollama_models},
            "qdrant_6333": tcp("127.0.0.1", 6333),
            "cloakserve_9222": tcp("127.0.0.1", 9222),
            "worker_go_available": go_exe.exists() or bool(os.environ.get("GO_BIN")),
        }

    # ---- actions ----------------------------------------------------------

    def run_pipeline(self, live_models=False, live_fetch_env=False, run_id="console-run"):
        """Full pipeline over pending batches. With no pending batches the
        FixtureFetcher seeds the deterministic sample (mock corpus)."""
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
                self.db, b["platform"], b["source"], b["thread_id"], b["comments"]
            )
        return {"ok": True, "queued_new_comments": queued}


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# imported late to avoid circulars at module import time
from jester.cli import (  # noqa: E402
    cmd_mark,
    cmd_migrate,
    cmd_resynth,
    cmd_retention,
    cmd_reembed,
    cmd_run,
    _vector_for,
    evaluate_doctor,
    evaluate_smoke,
)
