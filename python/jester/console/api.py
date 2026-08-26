"""§37.29 Operator console API: every jester feature surfaced as JSON.

Pure methods (no HTTP) so tests drive the console without sockets.
Actions wrap CLI behaviour and translate SystemExit into {ok, code} payloads.
"""
import json
import os
import re
import socket
import sqlite3
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
from jester.llm import GROQ_CHAT_MODELS, GroqClient, provider_for
from jester.config import (
    EMBEDDING_PROVIDERS,
    LLM_PROVIDERS,
    ROLE_PROVIDER_KEYS,
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
from jester.worker import go_binary, ingest as worker_ingest
from jester import schedule as _schedule
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
    cluster_members,
    count_nuggets,
    delete_cluster_idea,
    get_cluster,
    insert_cluster_idea,
    list_cluster_ideas,
    list_clusters,
    promote_cluster_idea,
    get_idea,
    run_activity,
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


#: Knobs whose value is a closed vocabulary rather than a number. The console
#: draws these as a <select>, so a new provider shows up in the UI the moment
#: it is valid in code rather than the next time someone edits the JavaScript.
_CHOICES = {
    "llm_provider": list(LLM_PROVIDERS),
    "embedding_provider": list(EMBEDDING_PROVIDERS),
    "competitor_search_provider": ["none", "fake", "duckduckgo"],
}


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

    def nuggets(self, limit=None):
        """Every archived nugget, newest first.

        The default used to be 500, applied AFTER loading and dicting every
        row, and the route never passed anything else — so an archive of 1,761
        showed 500 and counted itself as 500. Nothing said it had been cut.

        `total` is always the real count, so a caller that does pass a limit
        still reports the archive honestly rather than describing its own
        page as the whole of it (R55: no silent caps).
        """
        total = count_nuggets(self.db)
        cap = None
        if limit not in (None, "", 0, "0"):
            try:
                cap = max(1, int(limit))
            except (TypeError, ValueError):
                cap = None
        rows = list_nuggets(self.db, limit=cap)
        out = [self._rowdict(r) for r in rows]
        return {
            "ok": True,
            "nuggets": out,
            "total": total,
            "truncated": len(out) < total,
        }

    # ---- clusters (semantic regrouping) ---------------------------------

    def clusters(self):
        """Every theme the last clustering pass produced.

        No default cap — see `nuggets` for what a silent one costs.
        """
        rows = [self._rowdict(r) for r in list_clusters(self.db)]
        for r in rows:
            for key in ("platforms", "nugget_keys"):
                try:
                    r[key] = json.loads(r.get(key) or "[]")
                except (TypeError, ValueError):
                    r[key] = []
        stale = any(
            (r.get("embedding_model") or "").startswith("fake") for r in rows
        )
        return {
            "ok": True,
            "clusters": rows,
            "total": len(rows),
            # A pass run against hash vectors is noise wearing labels. It
            # cannot happen through this API, but a database carried over from
            # an older build can hold one, and it must be visibly marked
            # rather than silently trusted.
            "fake_embeddings": stale,
            "nuggets_total": count_nuggets(self.db),
        }

    def cluster(self, cluster_id: int):
        """One theme, its members, and any draft ideas made from it."""
        row = get_cluster(self.db, cluster_id)
        if row is None:
            return {"ok": False, "error": f"no cluster {cluster_id}"}
        d = self._rowdict(row)
        for key in ("platforms", "nugget_keys"):
            try:
                d[key] = json.loads(d.get(key) or "[]")
            except (TypeError, ValueError):
                d[key] = []
        d["members"] = [self._rowdict(m) for m in cluster_members(self.db, cluster_id)]
        d["ideas"] = [
            self._rowdict(i) for i in list_cluster_ideas(self.db, cluster_id)
        ]
        return {"ok": True, "cluster": d}

    def cluster_run(self, threshold=None, min_size=None, min_nuggets=None,
                    limit=None, run_id="console-cluster"):
        """Regroup the archive. The console's copy of `jester cluster`.

        Calls the same `cluster_archive` the CLI does rather than
        reimplementing the pass — the `jester schedule` lesson: behaviour that
        exists in only one caller is behaviour nobody can check.
        """
        from jester import clustering
        from jester.clustering import FakeEmbeddingRefused, cluster_archive

        def _num(v, default, cast=float):
            if v in (None, "", "0", 0):
                return default
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default

        try:
            return cluster_archive(
                self.db,
                self._cfg().thresholds,
                run_id=run_id,
                threshold=_num(threshold, clustering.DEFAULT_THRESHOLD, float),
                min_size=_num(min_size, clustering.DEFAULT_MIN_SIZE, int),
                min_nuggets=_num(min_nuggets, clustering.DEFAULT_MIN_NUGGETS, int),
                limit=_num(limit, None, int),
            )
        except FakeEmbeddingRefused as exc:
            # A refusal, not a crash. The operator needs the knob, not a 500.
            return {
                "ok": False,
                "error": str(exc),
                "fix": "set embedding_provider: ollama in config/thresholds.yaml, "
                       "make sure Ollama is reachable, then run this again",
            }
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            return {"ok": False, "error": f"clustering failed: {exc}"}

    def cluster_generate_idea(self, cluster_id: int):
        """Draft an idea FROM one theme.

        Deliberately writes to `cluster_ideas`, not `ideas`. Drafts are cheap
        and most get discarded; letting them into the archive directly would
        turn it into a scratchpad. Promotion is a separate, explicit act.
        """
        from jester.agents.critic import Critic
        from jester.agents.synthesizer import row_to_nugget
        from jester.llm import (
            model_name,
            select_critic_llm,
            select_synthesizer_llm,
        )
        from jester.models import Idea, IdeaScores

        row = get_cluster(self.db, cluster_id)
        if row is None:
            return {"ok": False, "error": f"no cluster {cluster_id}"}
        try:
            keys = json.loads(row["nugget_keys"] or "[]")
        except (TypeError, ValueError):
            keys = []
        if not keys:
            return {"ok": False, "error": "this cluster cites no nuggets"}

        self.db.row_factory = sqlite3.Row
        rows = self.db.execute(
            "SELECT * FROM nuggets WHERE unique_key IN (%s)"
            % ",".join("?" * len(keys)),
            keys,
        ).fetchall()
        if not rows:
            return {"ok": False, "error": "the cluster's nuggets are gone from the archive"}
        nuggets = [row_to_nugget(r) for r in rows]

        synth = select_synthesizer_llm(self._cfg().thresholds)
        draft = synth.synthesize(nuggets)
        idea = Idea(
            title=draft.title,
            problem_statement=draft.problem_statement,
            proposed_solution=draft.proposed_solution,
            supporting_nuggets=[n.unique_key for n in nuggets],
            synthesis_model=model_name(synth),
            scores=IdeaScores(),
        )
        # Critic.score persists ONLY when idea.id is set. A draft has none, so
        # this scores in memory and nothing reaches the ideas archive — which
        # is the whole point of the draft/promote split.
        critic = Critic(self.db, self._cfg().thresholds)
        idea = critic.score(idea, select_critic_llm(self._cfg().thresholds))

        draft_id = insert_cluster_idea(self.db, cluster_id, idea)
        return {
            "ok": True,
            "draft_id": draft_id,
            "idea": {
                "id": draft_id,
                "title": idea.title,
                "problem_statement": idea.problem_statement,
                "proposed_solution": idea.proposed_solution,
                "supporting_nuggets": idea.supporting_nuggets,
                "demand_signal": idea.scores.demand_signal,
                "feasibility": idea.scores.feasibility,
                "competition": idea.scores.competition,
                "overall": idea.scores.overall,
                "synthesis_model": idea.synthesis_model,
            },
            # Said out loud: a draft is not archived until somebody saves it.
            "detail": "draft only — not in the Ideas archive until you save it",
        }

    def cluster_save_idea(self, draft_id: int):
        """Promote one draft into the Ideas archive."""
        return promote_cluster_idea(self.db, int(draft_id))

    def cluster_discard_idea(self, draft_id: int):
        if delete_cluster_idea(self.db, int(draft_id)):
            return {"ok": True, "detail": f"draft {draft_id} discarded"}
        return {"ok": False, "error": f"no draft idea {draft_id}"}

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

    def _source_state(self):
        """When the worker last walked each source, keyed by name.

        `source_state` is one of the tables Go OWNS (§25.3/D-17), so a database
        that has never seen the worker simply does not have it. Missing is a
        normal state here, not an error — it means "nothing fetched yet".
        """
        try:
            rows = self.db.execute(
                "SELECT name, last_fetched_at, last_queued, visits FROM source_state"
            ).fetchall()
        except Exception:  # noqa: BLE001 - table absent until the worker runs
            return {}
        # Positional, not by name: row_factory is toggled between sqlite3.Row
        # and the default by other readers on this same connection, so a
        # name-keyed access here works or raises depending on call order.
        return {r[0]: {"last_fetched_at": r[1], "last_queued": r[2], "visits": r[3]}
                for r in rows}

    def sources(self):
        """The curated ingest list, plus the vocabulary the UI needs to render
        an add-form without hard-coding platform knowledge in JavaScript."""
        srcs = load_sources(self._sources_path)
        state = self._source_state()
        return {
            "ok": True,
            "path": str(self._sources_path),
            # The worker walks least-recently-fetched first, so "when was this
            # last visited" is now the thing that decides run order — it has to
            # be visible, or the rotation looks like randomness.
            "sources": [{**s.to_dict(), "state": state.get(s.name)} for s in srcs],
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
            # `_EDITABLE` is the write allow-list; this is the render list,
            # and they are not the same set. The per-role providers stay
            # writable through /api/thresholds but are drawn by the Agent
            # models card, which offers the three valid values as a dropdown
            # beside the model each one governs. Rendering them here too would
            # be a second, typo-prone place to set one thing.
            "editable": {
                key: {"type": caster.__name__, "range": Thresholds._RANGES.get(key),
                      "value": getattr(t, key), "choices": _CHOICES.get(key)}
                for key, caster in Thresholds._EDITABLE.items()
                if key not in ROLE_PROVIDER_KEYS
            },
            "scraper": scraper,
            "sources": [s.to_dict() for s in load_sources(self._sources_path)],
            "env": {
                "JESTER_QDRANT_URL": os.environ.get("JESTER_QDRANT_URL", ""),
                "JESTER_ORIGIN": os.environ.get("JESTER_ORIGIN", "manual"),
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
        # Which backends are in play once per-role overrides are resolved —
        # keying this off `llm_provider` alone missed the whole point of the
        # overrides, which exist so one role can use Groq while the rest do not.
        in_use = {provider_for(t, role) for role in ("extractor", "synthesizer", "critic")}
        groq_ready = None
        groq_choices = []
        if "groq" in in_use:
            # Ask Groq what this key can actually reach. An empty list means
            # "could not ask" (no key, no network), never "no models".
            groq_choices = GroqClient().list_models()
            groq_ready = bool(groq_choices)
            if not groq_choices:
                groq_choices = list(GROQ_CHAT_MODELS)
        current = {
            "extractor": t.extractor_model,
            "archivist": t.archivist_model,
            "synthesizer": t.synthesizer_model,
            "critic": t.critic_model,
            "embedding_model": t.embedding_model,
        }
        # "Missing" means "configured but not pulled", which is only a
        # question for an Ollama-served role. A Groq role has nothing to pull,
        # so flagging it would be a permanent false alarm.
        missing = set()
        if installed is not None:
            by_role = {
                "extractor": t.extractor_model,
                "synthesizer": t.synthesizer_model,
                "critic": t.critic_model,
            }
            for role, name in by_role.items():
                if provider_for(t, role) == "ollama" and name                         and not _model_installed(name, installed):
                    missing.add(name)
            # The archivist has no provider of its own; it follows llm_provider.
            if t.llm_provider == "ollama" and t.archivist_model                     and not _model_installed(t.archivist_model, installed):
                missing.add(t.archivist_model)
            # Embedding is Ollama's job regardless of the chat provider.
            if t.embedding_provider == "ollama" and t.embedding_model                     and not _model_installed(t.embedding_model, installed):
                missing.add(t.embedding_model)
        missing = sorted(missing)
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
            "llm_provider": t.llm_provider,
            "embedding_provider": t.embedding_provider,
            # Which backend actually serves each chat role once its override is
            # resolved. The UI must not re-implement that precedence.
            "role_providers": {
                role: provider_for(t, role) for role in ("extractor", "synthesizer", "critic")
            },
            "ollama_up": installed is not None,
            "installed": installed or [],
            # One catalogue per backend rather than one merged "available
            # models" list: which of these applies is a per-role question, and
            # the role's provider is the only thing that answers it.
            "groq_choices": groq_choices,
            "ollama_choices": installed or [],
            "groq_ready": groq_ready,
            # Anything configured but not pulled would fail at run time — the
            # UI marks these rather than letting the run discover it. Ollama
            # reports `name:latest`, so a bare `name` is the same model. Only
            # Ollama-served roles can be checked this way; Groq has nothing to
            # pull, so an unreachable Groq is reported as `groq_ready: false`
            # instead of marking every model "missing".
            "missing": missing,
        }

    def update_models(self, patch, profile=None):
        try:
            path = self._resolve_profile(profile)
            applied = save_models(path / "thresholds.yaml", patch)
        except (ConfigError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "applied": applied, "profile": path.name,
                "written_to": str(path / "thresholds.yaml")}

    def update_thresholds(self, patch, profile=None):
        """Write threshold keys back to thresholds.yaml.

        With no `profile` the shared facts mirror across config/ and
        config-live/, which is what the Config page's knobs want. With one,
        the write is scoped to that profile alone: a per-role provider is a
        profile's own choice, exactly like its model names, and mirroring it
        would erase the only difference between the two profiles.
        """
        try:
            if profile is not None:
                paths = [self._resolve_profile(profile) / "thresholds.yaml"]
            else:
                paths = self._mirror_paths("thresholds.yaml")
            written = []
            applied = {}
            for path in paths:
                applied = save_thresholds(path, patch)
                written.append(str(path))
        except (ConfigError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "applied": applied, "written_to": written,
                "profile": Path(paths[0]).parent.name}

    @staticmethod
    def _go_binary():
        """Resolve the Go toolchain. Kept as a method because tests and the
        infra probe both reach for it; the resolution itself lives in
        `jester.worker` alongside the code that runs the binary."""
        return go_binary()

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
            # A port check says a container is listening, not that anything
            # talks to it. For an entire day this panel showed Qdrant green
            # while every write went to a 32-dimension local file, because
            # JESTER_QDRANT_URL was unset. Report what the app is CONFIGURED
            # to use, and how wide the vectors in it actually are.
            "vectors": self._vector_state(),
            "cloakserve_9222": cloak_up,
            "worker_go_available": bool(go_bin),
            "go_bin": go_bin or "",
            # Live ingestion needs both halves; the UI gates its button on this.
            "can_ingest_live": bool(go_bin) and cloak_up,
        }

    def _vector_state(self):
        """Where vectors actually go, and whether that store is usable.

        `mode` is the honest headline: "server" only when JESTER_QDRANT_URL is
        set AND answers; "local file" otherwise, which is a working but
        single-process store that no other component shares.
        """
        url = os.environ.get("JESTER_QDRANT_URL") or ""
        state = {
            "mode": "server" if url else "local file",
            "url": url,
            "collection": "nuggets",
            "points": None,
            "dim": None,
            "embedding_provider": "",
            "embedding_dim": None,
            "usable": False,
            "detail": "",
        }
        try:
            t = self._cfg().thresholds
            state["embedding_provider"] = getattr(t, "embedding_provider", "")
            from jester.embed import select_embedding

            state["embedding_dim"] = int(getattr(select_embedding(t), "dim", 0))
        except Exception as exc:  # noqa: BLE001
            state["detail"] = f"cannot read embedding config: {exc}"
            return state

        if not url:
            state["detail"] = (
                "JESTER_QDRANT_URL is unset, so vectors go to a per-database "
                "file that no other process can share"
            )
            return state
        try:
            from qdrant_client import QdrantClient

            client = QdrantClient(url=url, timeout=5)
            if not client.collection_exists("nuggets"):
                state["detail"] = "server reachable; nuggets collection not created yet"
                state["usable"] = True
                return state
            info = client.get_collection("nuggets")
            state["points"] = int(info.points_count or 0)
            state["dim"] = int(info.config.params.vectors.size)
        except Exception as exc:  # noqa: BLE001
            state["detail"] = f"unreachable: {exc}"
            return state

        # The failure this exists to catch: a collection built for one vector
        # width while the configured embedder produces another. It reads as
        # healthy right up until the first upsert.
        if state["dim"] and state["embedding_dim"] and state["dim"] != state["embedding_dim"]:
            state["detail"] = (
                f"collection is {state['dim']}-dim but {state['embedding_provider']} "
                f"produces {state['embedding_dim']} — delete the store and re-embed"
            )
            return state
        state["usable"] = True
        if (state["embedding_provider"] or "").lower() == "fake":
            state["detail"] = (
                "embedding_provider is `fake`: these vectors are SHA-256 hashes "
                "and carry no meaning"
            )
        return state

    # ---- actions ----------------------------------------------------------

    # The goal types the console offers. `posts` and `comments` are ingestion
    # ceilings the Go worker enforces; `ideas` is a synthesis ceiling the
    # Python pipeline enforces. Anything else is refused rather than dropped —
    # a goal the operator set and the run ignored is the worst outcome.
    _GOAL_TYPES = ("posts", "comments", "ideas")

    def run_pipeline(self, live_models=False, run_id="console-run",
                     source_names=None, goal=None, ingest=None):
        """One operator-facing run: optionally ingest the selected sources,
        then run the Python agent pipeline over everything queued.

        The console's own copy used to do only the second half. That reads fine
        on day one and is inert forever after: `cmd_run` falls back to the
        FixtureFetcher when nothing is pending, the skip-list rejects every
        fixture comment the second time, and so each press completed in 0.2s
        having archived nothing. Pressing "run pipeline" has to be able to
        produce new work, which means it has to be able to fetch.

        `source_names` selects which curated sources to fetch (None/empty =>
        every enabled one). `goal` is `{"type": ..., "value": N}`.
        `ingest` forces the fetch half on or off; the default fetches whenever
        the operator named sources or the worker is reachable.
        """
        steps = []
        goal_type, goal_value = None, None
        if goal:
            goal_type = str(goal.get("type") or "").strip().lower()
            if goal_type in ("", "none"):
                goal_type = None
            elif goal_type not in self._GOAL_TYPES:
                return {"ok": False, "error": f"unknown goal type {goal_type!r}"}
            else:
                try:
                    goal_value = int(goal.get("value"))
                except (TypeError, ValueError):
                    return {"ok": False, "error": f"goal {goal_type!r} needs a whole number"}
                if goal_value < 1:
                    return {"ok": False, "error": f"goal {goal_type!r} must be at least 1"}

        names = [str(n).strip() for n in (source_names or []) if str(n).strip()]
        known = {s.name for s in load_sources(self._sources_path)}
        unknown = [n for n in names if n not in known]
        if unknown:
            return {"ok": False, "error": "no such source(s): " + ", ".join(sorted(unknown))}

        infra = self.infra()
        want_ingest = bool(names) if ingest is None else bool(ingest)
        if want_ingest and not infra.get("worker_go_available"):
            return {"ok": False,
                    "error": "the Go worker is not available, so the selected sources "
                             "cannot be fetched — install Go or set GO_BIN"}

        if want_ingest:
            step = self.ingest(
                live=bool(infra.get("can_ingest_live")),
                run_id=f"{run_id}-ingest",
                only=names,
                max_comments=goal_value if goal_type == "comments" else None,
                max_posts=goal_value if goal_type == "posts" else None,
            )
            steps.append({"step": "ingest", **step})
            if not step.get("ok"):
                # A failed fetch must not be followed by a pipeline pass that
                # reports "ok" over an empty queue.
                return {"ok": False, "steps": steps,
                        "error": "ingest failed: " + (step.get("error") or f"exit {step.get('code')}"),
                        "overview": self.overview()["counts"]}

        before = self._counts()
        args = type("Args", (), {})()
        args.db = self.db_path
        args.run = run_id
        args.max_ideas = goal_value if goal_type == "ideas" else None
        args.config = (
            str(self.repo_root / _LIVE_CONFIG_DIR)
            if live_models
            else self.config_dir
        )
        # Having just fetched, an empty queue is the answer, not a gap: seeding
        # the canned fixture here would report scraped counts for a run that
        # scraped nothing. A pipeline-only press keeps the demo fallback.
        args.seed_fixtures = not want_ingest
        old_origin = os.environ.get("JESTER_ORIGIN")
        os.environ["JESTER_ORIGIN"] = "manual"
        try:
            result = self._action_guard(cmd_run, args)
        finally:
            if old_origin is None:
                os.environ.pop("JESTER_ORIGIN", None)
            else:
                os.environ["JESTER_ORIGIN"] = old_origin

        after = self._counts()
        result["steps"] = steps
        result["overview"] = after
        result["archived"] = {
            "nuggets": after["nuggets"] - before["nuggets"],
            "ideas": after["ideas"] - before["ideas"],
        }
        # A run that archived nothing is the exact failure that made the
        # console feel broken, and it is NOT an error — say plainly what
        # happened so the next click is an informed one.
        if result.get("ok") and not any(result["archived"].values()):
            result["idle"] = True
            result["note"] = (
                "the run completed but archived nothing new — "
                + ("every fetched comment was already in the skip-list"
                   if want_ingest else
                   "nothing was queued to process; fetch some sources first")
            )
        return result

    def ingest(self, live=False, run_id="console-ingest", timeout=900,
               only=None, max_comments=None, max_posts=None):
        """Run the Go ingestion worker over the curated source list.

        The mechanics live in `jester.worker` so the scheduled job — which
        goes through the CLI, not the console — fetches exactly the same way.
        """
        return worker_ingest(
            self.config_dir, self.db_path, run_id,
            live=live, only=only, max_comments=max_comments,
            max_posts=max_posts, timeout=timeout, counts=self._counts,
        )

    # ---- schedule ---------------------------------------------------------

    #: Offered cadences. Free-form minutes are accepted too, but a picker with
    #: sane steps stops someone scheduling a live scrape every minute against a
    #: rate-limited API by mistyping one digit.
    SCHEDULE_PRESETS = (15, 30, 60, 120, 360, 720)

    def schedule(self):
        """What the host scheduler actually has registered — never a guess."""
        st = _schedule.status()
        log = self.repo_root / "data" / "schedule.log"
        tail = ""
        if log.is_file():
            try:
                # The scheduled run's own account of itself. schtasks records
                # an exit code and nothing else, so without this the console
                # could say "installed" and nothing about whether it works.
                tail = log.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                tail = ""
        return {
            "ok": True,
            "installed": st.get("installed", False),
            "scheduler": st.get("scheduler", ""),
            "next_run": st.get("next_run", ""),
            "cadence": st.get("cadence", ""),
            "interval_minutes": st.get("interval_minutes"),
            # How many times it has actually fired, and what that should have
            # been. A schedule that is registered and silent looks identical to
            # a healthy one until you put these two numbers side by side.
            "activity": self._schedule_activity(st.get("interval_minutes")),
            "detail": st.get("detail", ""),
            "presets": list(self.SCHEDULE_PRESETS),
            # What the REGISTERED task will fetch, read back out of the launcher
            # the scheduler actually runs — not what someone last typed into
            # the form. The two diverge the moment a schedule is installed from
            # the CLI or edited by hand.
            "options": _schedule.installed_options() if st.get("installed") else {},
            "scope_limits": {k: list(v) for k, v in self.SCOPE_LIMITS.items()},
            "sources": [
                {"name": s.name, "platform": s.platform, "kind": s.kind,
                 "enabled": s.enabled, "supported": s.supported}
                for s in load_sources(self._sources_path)
            ],
            "windows": _schedule.is_windows(),
            "task_name": _schedule.TASK_NAME,
            "log_path": str(log),
            "log_tail": tail,
            # The command a schedule runs is the whole point of the fix that
            # created this page: `cycle` fetches AND processes.
            "command": "jester cycle (ingest + pipeline)",
        }

    #: Depth ceilings the schedule form offers, with the range each accepts.
    #: An unattended job wants these far more than a manual one does: nobody is
    #: watching it decide to walk sixteen sources at 3am.
    SCOPE_LIMITS = {
        "max_posts": (1, 500),
        "max_comments": (1, 100000),
        "max_ideas": (1, 1000),
    }

    def _scrape_scope(self, options):
        """Validate a scope dict from the console. Returns (scope, error)."""
        options = options or {}
        names = [str(n).strip() for n in (options.get("only") or []) if str(n).strip()]
        if names:
            known = {s.name for s in load_sources(self._sources_path)}
            unknown = [n for n in names if n not in known]
            if unknown:
                return None, "no such source(s): " + ", ".join(sorted(unknown))
        scope = {"only": names}
        for key, (lo, hi) in self.SCOPE_LIMITS.items():
            raw = options.get(key)
            if raw in (None, "", 0, "0"):
                continue  # absent means no ceiling, which is not the same as 0
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return None, f"{key.replace('_', ' ')} must be a whole number"
            if not (lo <= value <= hi):
                return None, f"{key.replace('_', ' ')} must be between {lo} and {hi}"
            scope[key] = value
        return scope, None

    def _schedule_activity(self, interval_minutes):
        """How often the schedule actually ran, against how often it should have.

        The count alone cannot tell a healthy schedule from a broken one — this
        console spent months reporting "ok" for runs that archived nothing. The
        expected figure is what turns a number into a verdict.
        """
        act = run_activity(self.db, origin="scheduled")
        act["interval_minutes"] = interval_minutes

        # A window is only a fair yardstick for the time the schedule has
        # actually existed. Judging a job installed ten minutes ago against a
        # full 24h window reports it as having missed 47 of 48 ticks, which is
        # both false and the kind of red number people learn to ignore.
        first = self.db.execute(
            "SELECT MIN(started_at) FROM runs WHERE origin='scheduled'"
        ).fetchone()[0]
        act["first_run_at"] = first
        act["age_minutes"] = None
        if first:
            row = self.db.execute(
                "SELECT CAST((julianday('now') - julianday(?)) * 24 * 60 AS INTEGER)",
                (first,),
            ).fetchone()
            act["age_minutes"] = max(0, row[0] or 0)

        for hours, bucket in act["windows"].items():
            window_minutes = int(hours) * 60
            age = act["age_minutes"]
            if not interval_minutes or age is None:
                bucket["expected"] = None
                bucket["partial"] = False
                continue
            span = min(window_minutes, age)
            # +1 because the first run sits at the start of the span: a
            # 46-minute-old 30-minute schedule should have fired at t=0 and
            # t=30, which is two ticks, not one.
            bucket["expected"] = int(span // interval_minutes) + 1
            # True when the schedule is younger than the window, so the UI can
            # say "since the first run" rather than implying a full window.
            bucket["partial"] = age < window_minutes
        return act

    def schedule_install(self, every=None, at=None, options=None):
        """Register the recurring scrape.

        `every` is minutes, `at` is HH:MM, and `options` says WHAT to fetch and
        HOW DEEP — the half the scheduler had no way to express, so every
        unattended tick meant "every enabled source, no ceiling".
        """
        scope, err = self._scrape_scope(options)
        if err:
            return {"ok": False, "error": err}
        extra = _schedule.scrape_flags(scope)
        if every is not None:
            try:
                minutes = int(every)
            except (TypeError, ValueError):
                return {"ok": False, "error": f"interval {every!r} is not a whole number"}
            res = _schedule.install(db=self.db_path, config=self.config_dir,
                                    every=minutes, extra=extra)
        else:
            when = str(at or _schedule.DEFAULT_TIME).strip()
            if not re.match(r"^\d{1,2}:\d{2}$", when):
                return {"ok": False, "error": f"time {when!r} is not HH:MM"}
            res = _schedule.install(db=self.db_path, config=self.config_dir,
                                    at=when, extra=extra)
        return {"ok": res["ok"], "detail": res["detail"], "action": res["action"],
                "schedule": self.schedule()}

    def schedule_remove(self):
        res = _schedule.remove()
        return {"ok": res["ok"], "detail": res["detail"], "schedule": self.schedule()}

    def schedule_run_now(self):
        """Fire the registered task immediately — the only proof that the
        thing the host will run at 3am actually works."""
        res = _schedule.run_now()
        return {"ok": res["ok"], "detail": res["detail"]}

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
