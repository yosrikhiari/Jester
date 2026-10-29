# Jester — Current-State Assessment and Master Roadmap

> **Date:** 2026-10-25 · **Commit assessed:** `b175e32` (merge of PR #74) · **Branch:** `main`, clean
> **Method:** repository + documentation audit, targeted test runs, CI/Sonar state, and three targeted
> literature reviews (extraction/evaluation, dedup/classification, provenance/reproducibility).
> **Source of truth:** this document. It consolidates and, where they disagree with the code, supersedes
> `docs/CODEBASE_IMPROVEMENT_REPORT.md` (2026-10-14), `docs/plans/2026-08-31-real-estate-intelligence.md`
> and the console-redesign build order. It does not replace `docs/scope-and-access.md` (the owner's
> scope decision) or `docs/w1-pack/` (the recorded W1 proof).

---

## 0. Confidence and evidence rules used here

| Label | Meaning |
|---|---|
| **Verified** | Reproduced in this assessment: command output, test run, CI status, or direct code read. |
| **Documented** | Stated by a repo document; not independently re-run here. |
| **Inferred** | My reading of code/docs; could be wrong. |
| **Open** | Needs an experiment or a human decision. |

Nothing below claims a passing test or a completed feature without a corresponding verification in
Appendix A. Where a document and the code disagreed, the code wins and the discrepancy is listed as a
finding.

---

## 1. What Jester is, and what success means

**Objective (from `README.md` and `docs/scope-and-access.md`):** a local-first, single-operator system that
turns public community text into (a) scored product-idea candidates, (b) an auditable archive of
"problem signals" that identify buyers with a stated, budgeted need, and (c) reproducible research
write-ups assembled from measured runs.

**Intended user:** one technical operator (the owner) who runs it on a laptop or a small self-hosted
stack, reviews output, and occasionally hands a lead list or a published article to someone else.

**Definition of success, as the repo itself defines it** — three independent claims, each of which has to
survive review:

1. **Idea claim:** the extracted nuggets and synthesized ideas are better than the operator's own reading,
   or at least not worse, and the critic's scores track demand/feasibility rather than verbosity.
2. **Signal claim:** the deterministic classifier finds buyers at a rate that justifies the collection,
   with every verdict traceable to the words that produced it.
3. **Journal claim:** every number and quote in a published article traces to a run or a saved source,
   and the plan preceded the result.

The project already delivers (1)–(3) as *machinery*. What it does not yet deliver is **evidence that the
machinery works to a stated standard**, and one overdue scope decision that ratifies what may be collected.
That is the spine of the roadmap in §6.

---

## 2. Verified current state

### 2.1 Delivery milestones and what is proven

| Milestone | Date | State | Proof |
|---|---|---|---|
| W1 — fresh clone, one command twice, 5 rows then 0 new, 30-field map, 20/20 fixture gate | 28 Sep | **Met** | `docs/w1-pack/` with captured command output |
| Real-estate multi-region build (15 portals + 1 draft, 8 markets) | Sep | **Met** | `docs/realestate-compliance-audit.md` (all regions audited; Italy has no accessible portal) |
| Codebase hardening F-01…F-12 | 14 Oct | **Met** | `docs/CODEBASE_IMPROVEMENT_REPORT.md` Appendix C + PR #54 |
| Console redesign + curation behind flags (PR #74) | 25 Oct | **Merged** | `b175e32`; CI green including the Sonar quality gate |
| **Scope sign-off for the signal collector** | due 2 Oct | **Overdue** | `config/signal_rules.yaml`: `status: provisional`, `decided_on: ""` |
| **Quality measurement (extraction / dedup / rules recall)** | — | **Not started** | `eval/` holds 12+12 hand-written lines; dedup eval uses synthetic vectors |

### 2.2 Subsystem status

| Subsystem | What exists (verified) | What is missing | Status |
|---|---|---|---|
| **Ingestion (Go)** | 158 `platform:` entries in `config/sources.yaml`; adapters for Reddit, YouTube, HN, Discourse, Lemmy, Stack Exchange, GitHub, Steam, podcasts, 15 real-estate portals; `siteprofile` parser with ldjson/anchored/nextdata modes, detail tier, media hashing | No Reddit API client (0 OAuth references); browser-only, so the Reddit rooms stay blocked | Working, one deliberate gap |
| **Idea pipeline** | SQLite handoff → extractor → archivist (Qdrant) → synthesizer → critic; per-run provenance (model, prompt/rubric version); stand-ins signed and never archived as real; quota blocks per model | No extraction-quality evaluation; no labelled nugget set | Working, unmeasured |
| **Signals pipeline** | 5 keyless collectors; rules classifier with matched-words evidence; ClickHouse `ReplacingMergeTree` + `FINAL`; tri-way reconcile (`signals evidence`); buyer brief (`signals brief`); lead outcomes | Scope unratified; recall unmeasured; no Reddit collector by design | Working, unratified + unmeasured |
| **Journal** | State machine with 11 states and per-step gates; proof checks (citations, numbers, lint, origin); experiments with pre-registered plans and run fingerprints; publishing to a site branch; platform variants | No operator command to record post-publication metrics (`add_metric` exists, no CLI); `measured` therefore unreachable in practice; not exposed in the console | Built through J3, J8 stranded |
| **Console** | 14 pages, 3.9k lines JS / 1.2k CSS / 734 HTML; token auth on POSTs; 0.0.0.0 warning; access log; chat (off), specs, scheduler, maintenance behind flags | No journal page/API; JS has no test runner; accessibility debt (§5) | Working |
| **Ops** | CI (Python suite + coverage, Go build/test/coverage, frontend lint+build, Sonar scanner + quality gate), Dependabot, Docker Compose profiles | Floating image tags, root containers, default ClickHouse password (guarded but present); no `/metrics`, no tracing | Adequate for one operator, thin for diagnosis |

### 2.3 Measured size and quality (verified this run)

| Metric | Value |
|---|---|
| Python package | 27,870 lines across `python/jester/` |
| Python tests | 19,323 lines, 113 files, **1,665 passed / 14 skipped / 0 failed** (merged `main`) |
| Go | 18,212 lines, 35 test files; `go test ./...` all packages ok |
| Console static | `app.js` 3,966 · `app.css` 1,220 · `index.html` 734 |
| Largest Python modules | `cli.py` 3,536 · `api.py` 2,161 · `store.py` 1,318 · `edits.py` 1,036 |
| CI on `main` | success, 5m23s, Sonar gate pass (new-code coverage 91.4%, 0 new bugs, 0% duplication) |
| Open work | 6 Dependabot PRs; 4 stale remote branches; 1 React scaffold served as a placeholder sidecar |

### 2.4 What changed since the last assessment (14 Oct report)

- **Journal J2/J3 landed** (experiments, publishing) — the report predates both.
- **Model roles S1–S6 landed** (per-job provider/model, quota, console model card).
- **Signals grew** a buyer brief, hiring-advert collectors, lead outcomes, ClickHouse reconciliation.
- **Console redesign + curation merged** (this session), with chat/specs/scheduler/maintenance off by default.
- **Real-estate reached 15 portals + 1 draft**, with all compliance audits complete.
- **The F-list is closed**, but its two structural findings are only partly true now: `cli.py` is 3,536
  lines (was 3,923) and `api.py` 2,161 (was 2,663) — split started, not finished.

**Conclusion for planning:** the report's *backlog* is obsolete; its *method* (measure, then fix) is the
one to keep. This document replaces that backlog.

---

## 3. Research bibliography

Three targeted reviews were run for this assessment. Each full review (sources, per-question reasoning,
not-accessible list, and one cheap experiment per question) is committed beside this roadmap; the tables
below are the decision-relevant condensation. **All three rest on abstracts**: publisher full texts
returned 403 on every attempt, so findings are limited to what each abstract states. That limitation is
carried into every recommendation.

- `docs/research/2026-10-25-provenance-reproducibility.md` — provenance, reproducibility, claim traceability, publication metrics.
- `docs/research/2026-10-25-dedup-clustering-classification.md` — dedup thresholds, proxy benchmarks, clustering, rules vs models, ANN tuning.

### 3.1 Dedup, clustering, and classification (10 papers)

| Source | Year | Venue | Link | Method | Key finding | Jester consequence |
|---|---|---|---|---|---|---|
| Steck, Ekanadham & Kallus — *Is Cosine-Similarity of Embeddings Really About Similarity?* | 2024 | WWW '24 Companion | [10.1145/3589335.3651526](https://doi.org/10.1145/3589335.3651526) | Analytical study | Cosine can be arbitrary and non-unique; blind thresholding is unjustified | 0.87/0.92 are operating points, not properties — label and sweep |
| Abbas et al. — *SemDeDup* | 2023 | arXiv | [2303.09540](https://arxiv.org/abs/2303.09540) | k-means + cosine threshold, LAION/C4 | 50% removed with minimal loss; threshold fixed a priori, justified only downstream | Fix-then-validate is acceptable practice; document the operating point |
| Zhang, Baldridge & He — *PAWS* | 2019 | NAACL | [1904.01130](https://arxiv.org/abs/1904.01130) | 108k high-overlap paraphrase/non-paraphrase pairs | SOTA <40% accuracy before training | Best hard-negative source for the labelled pair set |
| Lee et al. — *Deduplicating Training Data…* | 2022 | ACL | [2107.06499](https://arxiv.org/abs/2107.06499) | Exact + MinHash at corpus scale | Operational "near-dup" = string overlap; misses paraphrase | Jester's semantic dedup is doing something stronger than the literature's baseline |
| Campello, Moulavi & Sander — *HDBSCAN* | 2013 | PAKDD | [10.1007/978-3-642-37456-2_14](https://doi.org/10.1007/978-3-642-37456-2_14) | Density hierarchy + stability extraction | No k; noise modelled explicitly; stability selects clusters | Matches Jester's "unclustered stays eligible"; keep it |
| Grootendorst — *BERTopic* | 2022 | arXiv | [2203.05794](https://arxiv.org/abs/2203.05794) | Embeddings → cluster → c-TF-IDF | c-TF-IDF gives competitive, cheap labels | Candidate deterministic replacement for the labeller model (P3-1) |
| Ratner et al. — *Snorkel* | 2017 | VLDB | [10.14778/3157794.3157797](https://doi.org/10.14778/3157794.3157797) | Labelling functions + label model | Within 3.6% of hand-curated labels, each rule inspectable | The hybrid path if rules ever need a model — keep rules as LFs |
| Buckley & Voorhees — *Retrieval Evaluation with Incomplete Information* | 2004 | SIGIR | [10.1145/1008992.1009000](https://doi.org/10.1145/1008992.1009000) | Analysis of pooled judgments | Measures not robust to gross incompleteness | Recall must come from sampling with CIs (G-05) |
| Malkov & Yashunin — *HNSW* | 2016/18 | arXiv | [1603.09320](https://arxiv.org/abs/1603.09320) | Hierarchical NSW graph | Log scaling; neighbour heuristic matters at high recall | Measure, don't inherit, index parameters |
| Aumüller, Bernhardsson & Faithfull — *ANN-Benchmarks* | 2018 | arXiv (SISAP'17) | [1807.05614](https://arxiv.org/abs/1807.05614) | Parameter-grid harness | Parameters must be swept; algorithms converge on similar trade-offs | `ef_search` measured on Jester's vectors (P3-2) |

**What this cluster does not support:** that per-category dedup thresholds beat a global one (no
head-to-head study was found), and any specific HNSW parameter values at Jester's scale. Both are recorded
as experiments, not adopted as facts.

### 3.2 Provenance, reproducibility, and traceability (10 sources)

| Source | Type/Year | Link | Key finding | Jester consequence |
|---|---|---|---|---|
| W3C PROV family | Standard, 2013 | [overview](https://www.w3.org/TR/prov-overview/) · [constraints](https://www.w3.org/TR/prov-constraints/) | Entity/activity/agent + derivation; validity = internal consistency, **not truth** | Jester's records already match the shape; export it as relations, skip RDF/PROV-O |
| Stodden, Seiler & Ma | Paper, 2018 | [10.1073/pnas.1708290115](https://doi.org/10.1073/pnas.1708290115) | 44% of artifacts obtainable, 26% reproduced | Capture at run time, never post hoc |
| Pimentel et al. | Paper, 2019 | [10.1109/MSR.2019.00077](https://doi.org/10.1109/MSR.2019.00077) | 1.4M notebooks: self-documenting ≠ re-executable | Local scripts drift silently |
| Raff | Paper, 2019 | [10.48550/arXiv.1909.06674](https://doi.org/10.48550/arXiv.1909.06674) | Code release "not sufficient" across 255 reimplementations | A commit fingerprint is necessary, not sufficient |
| Mitchell et al. — *Model Cards* | Paper, 2019 | [10.1145/3287560.3287596](https://doi.org/10.1145/3287560.3287596) | Record model version, intended use, metrics, eval conditions | Model+version recording is anchored; prompt/rubric is a defensible extension |
| Sadasivan et al. | Paper, 2023 | [arXiv:2303.11156](https://arxiv.org/abs/2303.11156) | Detection collapses under paraphrase; human text is spoofable | Do not build AI-text detection; record provenance instead |
| Baethge & Jergas | Meta-analysis, 2025 | [10.1186/s41073-025-00173-z](https://doi.org/10.1186/s41073-025-00173-z) | 16.9% of quotations wrong (8.0% major), no trend over time | The journal's citation gate is justified by a ~1-in-6 human baseline |
| Wadden et al. — *SciFact* | Paper, 2020 | [10.18653/v1/2020.emnlp-main.609](https://doi.org/10.18653/v1/2020.emnlp-main.609) | Verification = label **plus rationale span** | `[91.2%](run:12#accuracy)` is exactly the rationale-span idea |
| Kidwell et al. | Paper, 2016 | [10.1371/journal.pbio.1002456](https://doi.org/10.1371/journal.pbio.1002456) | Badges tied to a gate moved data reporting <3% → 23% | Gating publication on checks is behaviourally effective |
| Thelwall et al. | Paper, 2013 | [10.1371/journal.pone.0064841](https://doi.org/10.1371/journal.pone.0064841) | Readership signals weak-but-real; several platforms have none | J8 records first-party aggregates + evidence-link clicks, not social counts |

**What this cluster changes:** add a per-run **output hash** (P1-3). A fingerprint identifies the
environment and code; it can prove two runs differ, never that either is right — and it cannot detect a
nondeterministic rerun that produced different numbers under the same identity.

### 3.3 Extraction, scoring, and evaluation (10 papers)

| Source | Year | Venue | Link | Method | Key finding | Jester consequence |
|---|---|---|---|---|---|---|
| Gilardi, Alizadeh & Kubli | 2023 | PNAS | [10.1073/pnas.2305016120](https://doi.org/10.1073/pnas.2305016120) | 6,183 tweets/news, codebook tasks, ChatGPT vs MTurk vs 3 trained coders | +25pp over MTurk; run-to-run agreement 91–97%; but accuracy against human gold only **59–83%** | Extraction is plausible but the evidence is closed-set GPT-3.5/4 — not 8B local models |
| Boettinger, Bürgel & Bartsch | 2026 | Quality & Quantity | [10.1007/s11135-026-03036-x](https://doi.org/10.1007/s11135-026-03036-x) | 164 interviews, 752k words, manual vs AI coding | Low agreement; substantial **overcoding** (count and length); AI also found segments humans missed | Cap nuggets per comment and segment length; measure false negatives as well as false positives |
| Zheng et al. — *LLM-as-a-Judge* | 2023 | NeurIPS D&B | [arXiv:2306.05685](https://arxiv.org/abs/2306.05685) | 80 questions, 3k expert + 3k crowd votes | Position bias (65% / 23.8%); verbosity attack 91.3% vs 8.7%; **1–10 grading ≈ pairwise** (85% agreement, 60% with ties) | A 1–10 rubric is defensible; anchor it and randomise dimension order |
| Sharma et al. — *Sycophancy* | 2024 | ICLR | [arXiv:2310.13548](https://arxiv.org/abs/2310.13548) | 5 assistants, 4 free-form tasks | All five sycophantic; humans sometimes prefer the convincing wrong answer | Scores can flatter a verbose idea; demand the evidence, not just the number |
| Kim et al. — *Prometheus 2* | 2024 | EMNLP | [10.18653/v1/2024.emnlp-main.248](https://doi.org/10.18653/v1/2024.emnlp-main.248) | Open evaluator, 8 benchmarks | Rubric-conditioned direct assessment works; best among **open** evaluators only | Supports rubric scoring; does not support parity with humans |
| Si, Yang & Hashimoto | 2024 | arXiv (preprint) | [arXiv:2409.04109](https://arxiv.org/abs/2409.04109) | 100+ researchers; 49 LLM vs 49 human ideas, blind review | LLM ideas more novel, weaker feasibility; **LLM self-evaluation fails**; low diversity | Novelty and feasibility trade off — one "quality" number conflates them |
| Becker et al. | 2026 | JAIR 86 | [10.1613/jair.1.22258](https://doi.org/10.1613/jair.1.22258) | SLR of 257 papers | No task-agnostic metric; open-ended generation hardest to evaluate | The eval must be task-specific and multi-method |
| Cheng, Kasai & Yu — *Batch Prompting* | 2023 | EMNLP Industry | [10.18653/v1/2023.emnlp-industry.74](https://doi.org/10.18653/v1/2023.emnlp-industry.74) | 10 QA/NLI datasets, b = 2–6 | 5× token savings; **sharp drop at b = 6 in 4/5 datasets**; worse with long inputs | Batching beyond ~5 is unsupported; the current default b=1 is the evidence-backed choice |
| Chen, Zaharia & Zou | 2023 | arXiv (preprint) | [arXiv:2307.09009](https://arxiv.org/abs/2307.09009) | Same prompts, Mar vs Jun 2023 | GPT-4 prime/composite 84% → 51% in three months | Hosted models drift; record a snapshot date, not a tag |
| Biderman et al. | 2024 | arXiv (preprint) | [arXiv:2405.14782](https://arxiv.org/abs/2405.14782) | 3 years of harness practice | Results highly setup-sensitive; record exact revision, prompt, seeds, harness | The provenance triple is necessary but must add the model **digest** |

**Two corrections this cluster forces on the roadmap:**

1. **Batching (G-07) is not a quality lever at b = 5–20.** The reviewed evidence tested b = 2–6 and found
   degradation beginning at 6, worse with long inputs. The current `extractor_batch_size: 1` default is
   therefore *supported*, and the config's upper bound of 50 admits settings the literature does not
   support. The task changes from "promote batching" to "test b ∈ {1,3,5} and cap the knob".
2. **The evaluation designs must change.** Percent agreement is not agreement with humans (Gilardi's 97%
   is run-to-run consistency; accuracy is 59–83%). The critic's rubric is defensible, but the current
   `eval_gate` imputes an unchecked `competition` as 5, which turns "unknown" into "average" in the very
   benchmark used to judge the scorer. Report the unchecked rate and evaluate by blinded pairwise ranking,
   not by pass/fail counts.

**The caveat that bounds all of it:** every quality result above comes from GPT-3.5/4- or Claude-class
models. Nothing validates `qwen3:8b`, `llama3.1:8b` or `phi4-mini` for extraction or judging. The local
defaults are an untested extrapolation until P0-2 measures them — which is another reason P0-2 is the
first quality task, not a later one.

## 4. Research → implementation mapping

Each row is a finding that changes or validates a specific behaviour. "Task" points at the register in §6.

| Research finding | Component | What Jester does today | Change proposed | Task | How it is validated |
|---|---|---|---|---|---|
| Overcoding is the measured failure mode of LLM coding (Boettinger 2026) | `agents/extractor.py` | One call per comment, unbounded nugget count per comment | Cap nuggets/comment and insight length; measure false negatives on the triviality filter | P0-2 | nuggets/comment ≤ 1.2; fabrication ≤ 5%; F1 ≥ 0.70 |
| Human coding agreement is 59–83%, not 97% (Gilardi 2023) | evaluation harness | No labelled nugget set at all | Report **Cohen's κ** against human labels, never percent agreement | P0-2 | κ published beside P/R |
| 1–10 grading ≈ pairwise; position and verbosity bias are real (Zheng 2023) | `agents/critic.py`, `scoring.py` | Fixed rubric `0.4·d + 0.3·f + 0.3·(11−c)`, dimensions queried in fixed order | Anchor the scale (2/5/8 descriptors), randomise dimension order, refuse cross-model-version comparisons | P2-3 (new) | Judge-vs-human Spearman ≥ 0.7; SD across repeats ≤ 1.0 |
| Missing competition imputed as 5 converts "unknown" into "average" (finding from this review) | `console/api.py: eval_gate`, `scoring.py` | Unchecked competition becomes 5.0 and enters `overall` verbatim | Report the unchecked rate; exclude or stratify unchecked ideas in the benchmark; keep the production imputation | P2-3 | Benchmark states n checked/unchecked; no ranking claim across the two groups |
| Batching degrades at b = 6 on long inputs (Cheng 2023) | `agents/extractor.py`, config | `extractor_batch_size` defaults to 1, range 1–50 | Test b ∈ {1,3,5}; cap the knob at 5 unless a local measurement says otherwise | P1-4 (revised G-07) | Per-item F1 ≥ 0.95 of b=1 and Δfabrication ≤ 1pp, else keep b=1 |
| Cosine thresholds are operating points (Steck 2024); no head-to-head for per-category (search) | `dedup_threshold`, `dedup-sweep` | Global 0.87 / 0.92 | Sweep, then adopt per-category only on a ≥5-point precision gain with CI excluding zero | P0-3 | PR curve committed; PAWS sanity gate ≥ 0.8 F1 |
| Rules are auditable by construction; hybrid = rules as labelling functions (Ratner 2017) | `signals/filters.py` | Rules with stored matched words; recall unmeasured | Keep rules; add a second rule variant as capture 2; estimate recall with a CI | P1-5 (G-05) | Recall CI width ≤ 10 points on 300 negatives + 100 positives |
| HDBSCAN models noise; c-TF-IDF gives cheap labels (Campello 2013; Grootendorst 2022) | `clustering.py`, `labeller` | Connected-components clustering at 0.74; labels from a model | Keep the dependency-free clustering (documented choice); trial c-TF-IDF labels | P3-1 | Blind comparison of 50 cluster labels |
| Env+commit fingerprint cannot detect a nondeterministic rerun (Stodden 2018; Raff 2019) | `journal/lab.py` | Fingerprint = OS/Python/GPU/commit/dirty/seed | Add `output_sha256` over the run's result | P1-3 | Same fingerprint + different output ⇒ different hash |
| Hosted models drift; record a digest, not a tag (Chen 2023; Biderman 2024) | `llm.py`, `roles.py`, run provenance | Model **name** recorded (e.g. `qwen3:8b`), not digest or sampling params | Record model digest, quantisation, context size, temperature, seed; pin a Groq snapshot date | P2-4 (new) | Two runs after a tag re-pull are visibly different in provenance |
| Citation gates change behaviour when tied to verification (Kidwell 2016); human quote error is 16.9% (Baethge 2025) | `journal/checks.py` | Deterministic citation/number/lint/origin checks block review | Keep; cite the 16.9% baseline in the tool's own docs as the justification | P1-1 (docs line) | Checker rejections are counted and shown |
| Publication metrics are weak and platform-dependent (Thelwall 2013) | `journal` J8 | `channel_metric` table + `measured` gate; no command | Record only first-party aggregates, evidence-link clicks and the frozen window; allow an explicit `no_metrics` outcome | P1-1 | One article reaches `measured` with a stated window |

---

## 5. Gap and risk register

Impact is judged against the three success claims in §1, not against generic engineering taste.

### P0 — Critical (blocks a defensible claim)

| ID | Gap | Evidence | Impact if unresolved | Mitigation |
|---|---|---|---|---|
| **G-01** | **Scope decision never signed.** `config/signal_rules.yaml` carries `status: provisional`, `decided_on: ""`, `decision_due: "2026-10-02"`; 23 days overdue | file read | Every downstream statement about "buyers" rests on an unratified source list; the Reddit rooms stay unusable; the doc cannot leave PROVISIONAL | Owner signs one of two options: approve the five free buyer sources (HN who-is-hiring + job boards), or fund Reddit access. Record the decision in the YAML and the doc |
| **G-02** | **No quality baseline for extraction.** `eval/` is 12 good + 12 bad *hand-scored lines*; there is no labelled nugget set, so extractor precision/recall is unknown | `eval/known_good.md`, `known_bad.md`; no P/R harness in tests | Cannot honestly claim the extractor works, cannot detect a regression from a prompt/model change, cannot justify batching (G-07) | Hand-label 150–200 nuggets from the live archive (keep/discard + category) and publish precision/recall against the current prompt |
| **G-03** | **Dedup recall is explicitly unmeasured** (precision 71% on n=7 pairs) | `docs/realestate-dedup-validation.md` | Duplicate listings distort price/m², days-on-market and lead counts; a single global threshold (0.87) is known to be a compromise | Build a 100-pair labelled set (duplicate / not), measure P/R at 0.80–0.92, adopt per-category thresholds from `jester dedup-sweep` |
| **G-04** | **Journal `measured` is unreachable in practice.** `add_metric()` and the gate exist and are tested, but no CLI subcommand or console route calls it | `journal/__init__.py:506`, `states.py:200`, no `add_parser("measure")` | The journal's last state can only be reached by writing to SQLite by hand; "blocked" stays the honest answer forever | Add `jester journal measure SLUG --channel X --at DATE --views N [--points N] [--comments N]`, tests first |

### P1 — High (needed for the next milestone)

| ID | Gap | Evidence | Impact | Mitigation |
|---|---|---|---|---|
| **G-05** | Rules **recall unmeasured**; only yields per room are known | `docs/scope-and-access.md` tables; no sampling harness | Buyer counts are a floor of unknown tightness; a missed buyer is invisible | Two-capture estimate: an independently written second rule variant as capture 2, plus a stratified audit of 300 rule-negatives and 100 positives; report recall with a 95% CI, never a bare point estimate (Buckley & Voorhees 2004: incomplete judgment sets bias recall). If CI width > 10 points, expand the audit before claiming anything |
| **G-06** | **Observability is an access log.** 0 references to OTel/Prometheus; `metrics` appears only incidentally | code grep | A silent model failure looks like a quiet day; cost/latency per role is invisible; drift undetectable | Structured JSON log per request/job + `/metrics` (queue depth, LLM calls/latency/errors per role, cost estimate); defer tracing |
| **G-07** | **Extractor batching exists but is unproven**, and the knob's range exceeds what the evidence supports (1–50; tested range is 2–6 with degradation at 6) | `config.py:180,233`; `docs/research/2026-10-25-extraction-scoring-evaluation.md` §D | Either cost stays 1 call/comment, or batching ships with a quality claim it cannot support | Keep `b=1`; test b ∈ {1,3,5} on the P0-2 labels; cap the knob at the largest tested size (P1-4) |
| **G-08** | **Console has no journal surface** (0 journal references in `console/`) | code grep | The third subsystem is invisible to the operator UI; daily use drifts to CLI-only | Add read-only journal list/detail routes and one page; no state transitions from the UI |
| **G-09** | **JS is untested beyond selector existence.** 3,966 lines, 124 backend tests, no JS runner | `frontend/package.json` has no test script; only static-id guards in `test_console_api.py` | Every future UI change re-introduces the two defects found at merge (nav contrast, collapsed feed) | Vitest on pure helpers + a Playwright smoke in CI: load every page, assert no `pageerror`, assert nav contrast ≥4.5 and no card overflow |

### P2 — Medium

| ID | Gap | Evidence | Impact | Mitigation |
|---|---|---|---|---|
| **G-10** | Deployment hardening incomplete: `qdrant:latest`, `ollama:latest`, `cloakhq/cloakbrowser` unpinned; `go.Dockerfile` and `Dockerfile.python` declare no `USER` (run as root — only `Dockerfile.frontend` uses `USER node`); `CLICKHOUSE_PASSWORD` defaults to `jester` | `docker-compose.yml:39,163,235,216`; Dockerfiles | A silent upstream change breaks reproducibility; a mis-bound ClickHouse is open with a known password; root containers widen the blast radius | Pin digests; add a fail-fast on the default password outside loopback (a code guard already exists for one path); document the shared-SQLite uid constraint before adding `USER` |
| **G-11** | Accessibility debt: form-control borders ~1.3:1 (WCAG 1.4.11 wants 3:1); Runs/Ideas rows not keyboard-activatable; mojibake in two UI strings | measured this session at merge | WCAG AA non-conformance; keyboard-only operation incomplete | Token change for control borders; `tabindex` + Enter/Space on ledger rows; encoding sweep with the existing UTF-8 test extended to static assets |
| **G-12** | Documentation drift: README does not mention chat/specs/scheduler/maintenance (all merged, off by default); the React scaffold is still served as a Compose sidecar (`frontend` service, `:5173`) but renders a 29-line placeholder while the real UI is the vanilla console (`python/jester/console/` has 0 references to it); 4 stale branches (`journal-j3`, `yu-01-reddit-signals`, `fix/sonar-js-promises*`) | README; `docker-compose.yml:146-151`; `frontend/src/App.tsx`; branches | Operators cannot discover the new features; two UIs compete for contributors' attention; readers cannot tell live code from abandoned code | One documentation pass; decide the scaffold (promote or delete the Compose service and directory); verify-then-delete superseded branches |
| **G-13** | SQLite stays in `journal_mode=DELETE` by default; WAL is opt-in via env | `store.py:39` | Under concurrent worker+CLI+console, `SQLITE_BUSY` is a matter of timing; the repo already records one corruption incident | Soak test with WAL on; if clean, make WAL the default with a documented rollback |
| **G-14** | **The critic is uncalibrated against humans, and the benchmark silently imputes "unknown" competition as 5** | `scoring.py` (`IMPUTED_COMPETITION = 5.0`), `console/api.py: eval_gate`, `eval/` (12+12 lines) | Idea rankings cannot be defended as tracking demand/feasibility; a missing search result becomes an average score in the very set used to judge the scorer | Anchor the 1–10 scale, randomise dimension order, report checked/unchecked split, evaluate by blinded pairwise ranking (P2-3) |
| **G-15** | **Model identity is a name, not a digest.** No quantisation, context size, sampling parameters or Groq snapshot date in the run provenance | `llm.py`, `roles.py`, `models_used` JSON | A tag re-pull or a hosted model update changes results under an unchanged label; two runs are not comparable and nothing says so | Record digest + sampling params; pin the hosted snapshot date (P2-4) |
| **G-19** | **The console 500s on an archive older than the current schema, with an opaque error.** Observed live: the shell loads, then every `/api/*` call fails with `sqlite3.OperationalError: no such column: kind` | reproduced this session on `data/jester.db`; `console/server.py` opens with `migrate=False` by design | The operator sees a blank page and a 500 with no hint; the deliberate "open without reshaping" decision becomes indistinguishable from a bug | Startup preflight: check the required columns/`schema_version`, and if stale, serve a banner naming the migration command instead of failing each route (P2-7) |

### P3 — Low / exploratory

| ID | Gap | Note |
|---|---|---|
| **G-16** | Portal expansion incl. Italy | Italy has no accessible portal (documented dead end); adding more portals has diminishing value until dedup recall is measured |
| **G-17** | Chat enablement | Built, tested, off by default; enabling it is a product bet with no evidence yet |
| **G-18** | Per-run budget enforcement | Quota blocks exist per model; a hard per-run spend ceiling is not enforced |

---

## 6. Master execution roadmap

### 6.1 Phases and critical path

The dependency chain is: **ratify scope (G-01) → measure the baseline (G-02, G-03) → only then tune
thresholds or promote batching (G-07, and dedup thresholds)**, with observability (G-06) and the journal
command (G-04) in parallel because they gate nothing else.

```
G-01 scope sign-off ─┬─> G-05 rules recall ──> (signal claim defensible)
                     └─> G-02 extraction baseline ─┬─> G-07 batch promotion
                                                   └─> G-03 dedup recall ──> per-category thresholds
G-04 journal measure ──> journal 'measured' reachable ──> J8 evidence for one article
G-06 observability ────> (independent; enables cost/latency claims)
G-09 JS tests ─────────> (independent; protects every future UI change)
```

**Parallelisable:** G-01 (human), G-04, G-06, G-09, G-11, G-12.
**Sequential:** G-02 → G-07; G-03 → per-category thresholds; G-05 after G-01.

### 6.2 Task register

Effort is expressed in operator-days with the assumption of one experienced developer and no new
infrastructure. Uncertainty is called out where the work depends on labelling effort.

#### Phase A — Ratify and measure (the critical path)

| Field | P0-1 Scope sign-off |
|---|---|
| Priority | **P0** — everything the signal pipeline claims rests on it; it is a decision, not work |
| Task | Record the owner's decision on the collector's source scope in `config/signal_rules.yaml` and `docs/scope-and-access.md` |
| Current status | `status: provisional`, `decided_on: ""`, due 2026-10-02 |
| Rationale | Without it the doc cannot leave PROVISIONAL, the Reddit rooms remain barred, and no deliverable can cite the source list as approved |
| Evidence | `config/signal_rules.yaml:1-9`; `docs/scope-and-access.md` §6 (decision asked 29 Sep and 1 Oct) |
| Approach | Choose: (a) approve the five free buyer sources (HN who-is-hiring + Himalayas/Jobicy/Remotive) — the doc's own recommendation, since they produced 1,279 buyer signals from 594 companies in 60 days; or (b) fund Reddit commercial access for the four hiring rooms. Set `status: approved`, `decided_by`, `decided_on`; state the fallback in §7 |
| Dependencies | Owner only |
| Deliverable | Updated YAML + a dated decision paragraph in the scope doc |
| Acceptance | `status: approved`; the doc's header no longer says PROVISIONAL; the chosen source list matches what `jester signals sources` prints |
| Validation | `python -m jester.cli signals check` still 20/20; `signals sources` output matches the doc |
| Effort | 1 hour (owner), 15 min (edit) |

| Field | P0-2 Extraction quality baseline |
|---|---|
| Priority | **P0** — the extractor is the pipeline's first and most consequential model; nothing downstream can be trusted without it |
| Task | Build a labelled nugget set and a precision/recall harness for the extractor, and publish the first numbers |
| Current status | No labelled set; `eval/` covers idea *scoring* only (12+12 lines) |
| Rationale | Enables honest claims, regression detection on prompt/model changes, and the only defensible basis for batching. It also resolves the largest unstated assumption in the project: **no reviewed study validates an 8B local model for this task**, so the default `qwen3:8b` extractor is currently an untested extrapolation |
| Evidence | `eval/known_good.md` (12 data lines), `known_bad.md` (12); no P/R harness in `python/tests/`; `docs/research/2026-10-25-extraction-scoring-evaluation.md` §A (per-comment F1 ≥ 0.70, nuggets/comment ≤ 1.2, fabrication ≤ 5%) |
| Approach | (1) Sample 200 comments stratified by platform from the archive. (2) Hand-label each: keep/discard, category, and the span that justifies it; adjudicate disagreements and re-label 20% after 24h to measure intra-rater stability. (3) Store as `eval/nuggets_labelled.jsonl` with labels + rater id. (4) Harness: run the extractor over the same comments with the frozen prompt, match by `unique_key`, compute **Cohen's κ** against the human labels plus per-item F1, nuggets-per-comment ratio and a fabricated-nugget count (a nugget with no supporting span). (5) Report the prompt/model versions and the model **digest** |
| Dependencies | Archive access (a populated `data/jester.db`; the local one holds 3 nuggets — use the production archive or re-collect) |
| Deliverable | `eval/nuggets_labelled.jsonl`, `python/tests/test_extractor_quality.py` (skipped unless labels present), `docs/quality/extraction-2026-11.md` |
| Acceptance | ≥150 labelled comments; **κ, F1, nuggets/comment and fabrication rate** all reported with denominators; F1 ≥ 0.70, ratio ≤ 1.2 and fabrication ≤ 5% to call the extractor "validated"; otherwise the report says which threshold failed |
| Validation | Re-running the harness twice on the same prompt yields the same labels for ≥95% of rows (deterministic stand-in) or a stated variance range (live model); the report states the model digest |
| Effort | 2–3 days (labelling dominates); ±1 day if category definitions need tightening |

| Field | P0-3 Dedup recall and per-category thresholds |
|---|---|
| Priority | **P0** for the signal claim (duplicates inflate lead counts), **P1** for real estate |
| Task | Measure dedup recall on a labelled pair set and decide, per category, whether a local threshold beats the global one |
| Current status | Precision 71% on 7 pairs; recall explicitly unmeasured; one global 0.87 |
| Rationale | The scope doc's buyer counts and the real-estate analytics both assume duplicates are removed. A cosine threshold is an **operating point, not a property of the model** (Steck et al. 2024), so the current values are unvalidated conventions |
| Evidence | `docs/realestate-dedup-validation.md` ("recall: not measured"); `thresholds.yaml: dedup_threshold: 0.87`; `docs/research/2026-10-25-dedup-clustering-classification.md` §A–B |
| Approach | (1) 100 pairs per content type: positives from the real-estate harvest (same listing across portals), hard negatives from PAWS-style near-miss pairs (high lexical overlap, different meaning). (2) Sweep 0.70–0.95 via `dedup-sweep`, plot P/R. (3) **Adopt a per-category threshold only if precision at matched recall improves ≥5 points with a CI excluding zero**; otherwise keep the global value and say why. (4) Run PAWS/QQP as a *sanity gate only* (F1 < 0.8 means the pipeline is broken) — never import a threshold from a proxy benchmark |
| Dependencies | P0-1 only for the comment-side labels; real-estate labels already exist in the harvest |
| Deliverable | `docs/quality/dedup-2026-11.md` + per-category config keys, or a documented decision to keep the global |
| Acceptance | P/R published with n and CIs; every retained/changed threshold justified by the curve; no category left on the global default without a stated reason |
| Validation | `jester dedup-sweep` reproduces the chosen operating points; PAWS sanity gate ≥ 0.8 F1 |
| Effort | 2 days |

#### Phase B — Close the journal loop (parallel to A)

| Field | P1-1 `journal measure` command |
|---|---|
| Priority | **P1** |
| Task | Expose `add_metric` as a CLI command (and a read-only console route later) |
| Current status | `add_metric` implemented and unit-tested; no parser, no route |
| Rationale | Without it the `measured` gate can only be satisfied by hand-written SQL; the journal's promise ends at `shared` |
| Evidence | `journal/__init__.py:506`; `states.py:200-214`; `cli.py` journal parsers (no measure) |
| Approach | TDD: test the parser + the recorded row + the gate transition; command `journal measure SLUG --channel CH --at ISO --views N [--points N] [--comments N]`; refuse when the article has no post on that channel; print the next blocked reason |
| Dependencies | None |
| Deliverable | Command, tests, README line in the journal section |
| Acceptance | A journal posted on two channels reaches `measured` after two dates' metrics per channel |
| Validation | `pytest python/tests/test_journal.py -k measure`; one manual run against a throwaway journal db |
| Effort | 0.5 day |

| Field | P1-3 Run output hash |
|---|---|
| Priority | **P1** |
| Task | Record a content hash of each experiment run's output, alongside the existing environment/code fingerprint |
| Current status | `journal/lab.py: fingerprint()` records OS, Python, machine, CPU count, GPU/driver, commit, dirty flag and seed — but nothing about what the run **produced** |
| Rationale | Reproduction failures are dominated by non-runnable context, and code availability alone does not establish reproducibility (Stodden 2018: 44% of artifacts obtainable, 26% reproduced; Raff 2019: code release "not sufficient"). A fingerprint proves two runs *differ*, never that either is *right*; the output hash is the only cheap detector of same-fingerprint-different-result — the exact failure a nondeterministic kernel or GPU produces |
| Evidence | `docs/research/2026-10-25-provenance-reproducibility.md` §2B and §3 |
| Approach | Add `output_sha256` to `experiment_run` via the existing `_ADD_COLUMNS` path; compute it at write time over the canonical JSON of `result` plus `stdout`/`stderr`; expose it in `journal show` and in the table/figure provenance lines; back-fill nothing (old runs keep `NULL` and say so) |
| Dependencies | None; independent of P1-1 |
| Deliverable | Column + writer + `journal show` line + test |
| Acceptance | Two runs of the same command with identical fingerprints but different output have different `output_sha256`; identical output has identical hashes; old rows render as "not recorded" |
| Validation | A test that writes two fake runs (same fingerprint, different `result.json`) and asserts the hash differs |
| Effort | 0.5 day |

| Field | P1-2 Observability slice |
|---|---|
| Priority | **P1** |
| Task | Structured request/job logs and a `/metrics` endpoint with the five numbers that matter |
| Current status | Access log only; no metrics/tracing (0 references) |
| Rationale | A silent model failure currently looks like a quiet day; cost per role is invisible |
| Evidence | code grep for OTel/Prometheus returns 0; `server.py` access log |
| Approach | JSON lines (ts, route, status, ms, run_id); counters/gauges: queue depth, batches pending, LLM calls and errors per role, p50/p95 latency per role, estimated tokens/cost per run; expose Prometheus text at `/metrics`; no tracing yet |
| Dependencies | None |
| Deliverable | `/metrics`, log format doc, one dashboard query page in the console (optional) |
| Acceptance | A forced model failure increments an error counter and shows in the log; queue depth matches `/api/overview` |
| Validation | Kill Ollama mid-run and confirm the counter moves; compare `/metrics` queue depth to the DB |
| Effort | 1–2 days |

#### Phase C — Protect the UI and pay down debt (parallel)

| Field | P2-1 JS test harness + UI regression smoke |
|---|---|
| Priority | **P2** |
| Task | Vitest for pure helpers; Playwright smoke in CI for every page |
| Current status | No JS runner; static-id guards only |
| Rationale | The merge proved two visual defects can pass 1,665 tests; the next UI change needs a net |
| Evidence | `frontend/package.json` (no test script); the two defects fixed at merge |
| Approach | Vitest on formatters/state helpers extracted from `app.js`; Playwright: load 14 pages, assert no `pageerror`, nav link contrast ≥4.5:1, no card overflow, rows-above-fold snapshot per page |
| Dependencies | None |
| Deliverable | `npm test`, `tests/ui/smoke.spec.ts`, CI job |
| Acceptance | A deliberately broken selector or a low-contrast token fails CI |
| Validation | Revert one of the merge fixes locally and confirm the smoke fails |
| Effort | 2 days |

| Field | P2-2 Accessibility and encoding debt |
|---|---|
| Priority | **P2** |
| Task | Control-border contrast, keyboard-activatable ledger rows, mojibake sweep |
| Current status | Borders 1.3:1; runs/ideas rows mouse-only (documented pre-existing); two strings mis-encoded |
| Rationale | WCAG 1.4.11 non-conformance in a tool used daily; keyboard operation is a stated improvement already half-built |
| Evidence | measured at merge (form controls 1.31:1); `ADAPTATION-DEVIATIONS.md` notes the row gap |
| Approach | Add `--line-control` token at ≥3:1 and use it on input/select/textarea; extend the existing `tabindex` pattern from Posts to Runs/Ideas; add a static-asset encoding test to the existing UTF-8 check |
| Dependencies | None |
| Deliverable | Token change, row handlers, tests |
| Acceptance | Automated contrast check passes; Tab+Enter opens a run detail; no replacement characters in served assets |
| Validation | The P2-1 smoke plus a keyboard scripted pass |
| Effort | 1–2 days |

#### Phase D — Hardening and hygiene (after the critical path)

| Field | P1-4 Batching: test the size, cap the knob |
|---|---|
| Priority | **P1** |
| Task | Evaluate `extractor_batch_size` at b ∈ {1,3,5} and cap the configurable maximum |
| Current status | Default 1, range 1–50; the only gate is an equality check on one fixture |
| Rationale | The reviewed evidence tested b = 2–6 and found a sharp accuracy drop at 6 on four of five datasets, worse with long inputs; b = 20–50 in the current range is extrapolation. Batching is a **cost** lever, not a proven quality lever |
| Evidence | `docs/research/2026-10-25-extraction-scoring-evaluation.md` §D (Cheng 2023); `config.py:180,233` |
| Approach | Run the P0-2 labelled set at b ∈ {1,3,5} with a fixed prompt and temperature; measure per-item F1 against b=1 plus fabricated-nugget count and tokens; promote only if F1 ≥ 95% of b=1 and fabrication rises ≤ 1 point; cap the knob at the largest tested size |
| Dependencies | P0-2 (the labelled set) |
| Deliverable | `docs/quality/batching-2026-11.md` + a capped range + a stated default |
| Acceptance | The report gives per-b numbers for F1, fabrication and tokens; the config's upper bound equals the largest tested size |
| Validation | Re-run both sizes on the same set; the winner is reproducible |
| Effort | 1 day (after labels exist) |

| Field | P1-5 Rules recall with a confidence interval |
|---|---|
| Priority | **P1** |
| Task | Estimate the buyer-signal rule set's recall by two-capture sampling and a stratified audit |
| Current status | Yields per community are known; recall is not measured anywhere |
| Rationale | A buy-side claim ("we found 1,279 buyer signals") is a floor of unknown tightness; incomplete judgment sets bias recall measures, so the estimate needs sampling and a CI, not a guess |
| Evidence | `docs/research/2026-10-25-dedup-clustering-classification.md` §D (Buckley & Voorhees 2004; Ratner 2017); `docs/scope-and-access.md` |
| Approach | (1) Write a second, independently worded rule variant over the same fields. (2) Treat rules-A and rules-B as two captures over 1,000 sampled records; estimate missed positives by capture–recapture. (3) Audit 300 records neither variant flagged plus 100 it did, hand-labelling buyer/not. (4) Report recall with a 95% CI and state the independence assumption explicitly, since it is untestable here |
| Dependencies | P0-1 (scope sign-off) |
| Deliverable | `docs/quality/rules-recall-2026-11.md` |
| Acceptance | Recall reported with CI and n; CI width ≤ 10 points; the two captures and the audit set are committed |
| Validation | A second analyst reproduces the estimate from the committed sample |
| Effort | 1.5 days |

| Field | P2-3 Critic calibration |
|---|---|
| Priority | **P2** |
| Task | Anchor the 1–10 rubric, randomise dimension order, and fix the benchmark's treatment of unchecked competition |
| Current status | Fixed rubric, fixed dimension order, eval set of 12+12 lines; `competition=None` is imputed as 5 inside the benchmark |
| Rationale | Direct 1–10 grading tracks human judgement about as well as pairwise, so the rubric choice is defensible — but the scale is unanchored, order effects are documented, and imputing "unknown" as "average" silently turns a gap into a measurement |
| Evidence | `docs/research/2026-10-25-extraction-scoring-evaluation.md` §B (Zheng 2023; Prometheus 2); `scoring.py`; `console/api.py: eval_gate` |
| Approach | Add 2/5/8 descriptors to the critic prompt; shuffle dimension order per call and record the order; in `eval_gate`, report the checked/unchecked split and evaluate by blinded pairwise ranking (does the pipeline rank known-good above known-bad) with a rank correlation, not pass/fail counts |
| Dependencies | None (independent of P0-2) |
| Deliverable | Prompt change, benchmark change, `docs/quality/critic-2026-11.md` |
| Acceptance | Judge-vs-human Spearman ≥ 0.7 on ≥20 ideas; repeat-run SD ≤ 1.0; the benchmark prints checked/unchecked counts and an AUC |
| Validation | Three repeated scorings of the same ideas; human ranking of the same set, blinded |
| Effort | 1.5 days |

| Field | P2-4 Model identity in provenance |
|---|---|
| Priority | **P2** |
| Task | Record the model **digest** and sampling parameters, not only the model name |
| Current status | Runs record `qwen3:8b` / `openai/gpt-oss-120b`; no digest, quantisation, context size, temperature or seed in the run row |
| Rationale | Hosted models drift measurably over months, and a mutable Ollama tag can be re-pulled to different weights; a name is not an identity. This is the difference between a provenance record and a label |
| Evidence | `docs/research/2026-10-25-extraction-scoring-evaluation.md` §E (Chen 2023; Biderman 2024); `docs/research/2026-10-25-provenance-reproducibility.md` §3 |
| Approach | Capture the resolved digest (Ollama `/api/tags` digest; Groq model id + snapshot date), quantisation and the effective sampling parameters into the run's `models_used` JSON; display them in `jester runs`/`models`; keep the old fields for compatibility |
| Dependencies | None |
| Deliverable | Provenance fields + tests + a documented compatibility rule |
| Acceptance | Two runs after a tag re-pull differ in the recorded digest; `jester models --diff` surfaces it |
| Validation | Mock an Ollama digest change and assert the run record differs |
| Effort | 1 day |

| Field | P2-5 Deployment hardening · P2-6 Documentation and branch cleanup · P2-7 Console schema preflight |
|---|---|
| Priority | **P2 / P2 / P2** |
| Task | Pin image digests, fail fast on default secrets, document the non-root constraint · README pass for chat/specs/scheduler/maintenance, decide the React scaffold, verify-then-delete superseded branches · make the console say "this archive predates the schema; run the migration" instead of returning 500 on every route |
| Evidence | `docker-compose.yml` pins; README gaps; branches confirmed superseded (the `urlsplit` fix is already in `main`; `yu-01` is an older superset of merged signals work); the stale-archive 500 reproduced live this session |
| Acceptance | No floating tags; `README` lists every shipped subsystem; `git branch -r` shows only live work; a stale DB yields one clear, actionable message and the rest of the UI still renders |
| Effort | 2.5 days total |

---

#### Phase E — Evidence-backed opportunities (only after A–C)

| Field | P3-1 Deterministic cluster labelling (c-TF-IDF) |
|---|---|
| Priority | **P3** |
| Task | Evaluate replacing the labeller model with a class-based TF-IDF label, keeping the model as fallback |
| Current status | Every cluster name is a model call (role `labeller`) |
| Rationale | BERTopic's c-TF-IDF produces competitive labels deterministically and cheaply; a deterministic labeller removes one model call, one failure mode and one source of run-to-run variance from a pipeline whose selling point is auditability |
| Evidence | `docs/research/2026-10-25-dedup-clustering-classification.md` §C (Grootendorst 2022) |
| Approach | Compute c-TF-IDF over each cluster's nuggets; produce a 3–5 word label; run both labellers over 50 existing clusters and have the operator blind-pick the better label; adopt only if c-TF-IDF wins or ties |
| Deliverable | Comparison sheet + switch behind `labeller_mode: ctfidf|model` |
| Acceptance | The blind comparison is recorded; if adopted, `cluster` produces the same labels on a re-run |
| Effort | 1 day |

| Field | P3-2 Measure HNSW recall instead of inheriting defaults |
|---|---|
| Priority | **P3** (becomes P1 at 10^6 vectors) |
| Task | Measure recall@10 against exact search on Jester's own vectors and record the chosen ef_search |
| Current status | Qdrant defaults; `INDEXING_THRESHOLD=1000` was tuned by a one-off measurement |
| Rationale | ANN parameters must be swept, not assumed (Aumüller 2018); `ef_search` is a runtime knob while `M`/`ef_construction` are build costs |
| Evidence | `docs/research/2026-10-25-dedup-clustering-classification.md` §E |
| Approach | 1,000 sampled queries, exact top-10 vs index top-10, sweep `ef_search` ∈ {32,64,128,256}; record recall/latency; re-check when the collection crosses 10^6 |
| Acceptance | A table of recall@10 vs p95 latency is committed; the chosen `ef_search` is in config with the measurement beside it |
| Effort | 1 day |

#### Deliberately rejected (recorded so they are not re-litigated)

| Direction | Why rejected | Evidence |
|---|---|---|
| Adopt per-category dedup thresholds *now* | No published head-to-head shows they beat a global threshold; "plausible, untested, cheap to test" is not the same as "adopt" | `docs/research/2026-10-25-dedup-clustering-classification.md` §A |
| Replace the rules classifier with a model | Rules store their own justification (the matched words) and are reproducible by construction; a model loses the rationale and gains an unmeasurable drift mode. Hybrid (rules as labelling functions) stays available if a model is ever trained | §D; Snorkel (Ratner 2017) |
| Full W3C PROV / PROV-O export | Jester's records already have PROV's shape (entity/activity/agent + derivation); RDF, bundles and provenance-of-provenance add cost with no reader | `docs/research/2026-10-25-provenance-reproducibility.md` §2A |
| AI-text detection for the journal | Detection collapses under paraphrase and is spoofable; recording model/prompt/rubric versions is the durable substitute | §2C (Sadasivan 2023) |
| LLM-as-claim-checker in the journal | Deterministic exact-match against a stored run has zero error by construction; an LLM checker would inherit the paraphrase fragility and a 1-in-6 human error baseline | §2D (Baethge & Jergas 2025) |
| A publication analytics dashboard | Readership signals are weak and platform-dependent; the only decision-relevant number is whether anyone followed a cited evidence link | §2E (Thelwall 2013) |
| Optimising silhouette / Davies–Bouldin for clusters | Both reward convex, equal-variance clusters and punish noise — the opposite of what HDBSCAN is chosen for | Dedup review §C |

## 7. Immediate action checklist

1. **Sign the scope decision (P0-1, 1 hour).** Edit `config/signal_rules.yaml` (`status: approved`, `decided_by`,
   `decided_on`) and replace the PROVISIONAL banner in `docs/scope-and-access.md` with the decision and the
   fallback. *Done when* `signals sources` matches the approved list and the doc stops saying provisional.
2. **Start the extraction label set (P0-2, first 50 of 200).** Create `eval/nuggets_labelled.jsonl` with the
   schema `{unique_key, platform, keep, category, span, rater, labelled_at}` and record the sampling query.
   *Done when* 50 rows exist and the harness computes κ, F1, nuggets/comment and fabrication over them.
3. **Add `journal measure` (P1-1, half a day).** TDD the parser, then use it on a real posted article.
   *Done when* a journal reaches `measured` without hand-written SQL.
4. **Ship `/metrics` (P1-2, 1–2 days).** Five numbers, no tracing.
5. **Cap `extractor_batch_size` at 5 and record why (P1-4, 1 hour).** One config bound plus a comment citing
   the batching evidence; the full b∈{1,3,5} comparison waits for the labels.
6. **Verify-then-delete the stale branches (P2-6, 1 hour).** `feat/journal-j3` is superseded (the `urlsplit`
   fix is in `main`); `feat/yu-01-reddit-signals` is an older tree; `fix/sonar-js-promises*` are merged.

**Next milestone — "Measured" (definition of done):**

- The collector's scope is `approved` in config and doc.
- One published quality report exists with real denominators: extraction P/R on ≥150 labelled comments,
  dedup P/R on ≥100 labelled pairs, rules recall with a confidence interval on ≥100 sampled non-buyers.
- `jester journal measure` works and one article is in `measured` with 7-day and 30-day numbers recorded.
- `/metrics` exposes queue depth, per-role LLM errors and latency.
- The UI smoke test runs in CI and would catch the two defects found at merge.

---

## 8. Decisions required from the owner

| # | Decision | Why it can't be made here | Default if unanswered |
|---|---|---|---|
| D-1 | Approve the five free buyer sources, or fund Reddit commercial access | Commercial/legal commitment | Stay provisional; signal claims stay unratified; the free sources keep collecting |
| D-2 | Accept that "quality" means measured P/R on hand-labelled samples (≈2–3 days of labelling) | Time commitment | No quality claim can be made; tuning stays guesswork |
| D-3 | Keep or delete the React scaffold sidecar (`frontend` Compose service on `:5173`, currently a placeholder) | Product direction (already decided in practice in favour of the vanilla console) | It stays as a second, empty UI and confuses contributors |
| D-4 | Enable chat? | Product bet with no evidence | Stays off; code remains tested but unused |

---

## 9. What this document supersedes, and what it does not

**Supersedes:** the *backlog* in `docs/CODEBASE_IMPROVEMENT_REPORT.md` (F-01…F-12 are closed) and the
now-executed build order in `docs/console-redesign/2026-09-22-reddit-scraping-and-redesign.md` §5.

**Does not supersede:** `docs/scope-and-access.md` (the owner's scope record), `docs/w1-pack/` (recorded
proof), `docs/realestate-compliance-audit.md` (compliance is a standing constraint), the
`ADAPTATION-DEVIATIONS.md` design ledger, and the three research reviews under `docs/research/` (they are
the evidence this roadmap cites; if one is challenged, the roadmap's affected rows change, not the other
way round).

---

## Appendix A — Verification log

| Check | Command | Result |
|---|---|---|
| Branch state | `git log -1`, `git status -sb` | `b175e32`, clean except pre-existing untracked `download6.png` |
| Python suite (merged `main`) | `python -m pytest python/tests -q --ignore=…/test_e2e_changes.py` | 1,665 passed, 14 skipped, 0 failed (9m) |
| E2E journeys | `pytest python/tests/test_e2e_changes.py` | 5 passed |
| Go suite | `go test ./...` | all packages ok |
| CI on `main` | `gh run list --branch main` | success, 5m23s |
| Sonar gate | `gh pr checks 74` + SonarCloud API | pass: 0 new bugs, 91.4% new-code coverage, 0% duplication |
| Scope status | `config/signal_rules.yaml` | `status: provisional`, `decided_on: ""`, due 2026-10-02 |
| Journal metric gap | grep for `add_parser("measure")`, `add_metric` | function + gate + tests exist; no command |
| Console journal gap | grep `journal` in `python/jester/console/` | 0 references |
| Observability | grep OTel/Prometheus/structlog | 0 references |
| Dedup recall | `docs/realestate-dedup-validation.md` | precision 5/7 = 71%; recall "not measured" |
| Eval size | line counts of `eval/known_good.md`, `known_bad.md` | 12 + 12 data lines |
| Image pins | `docker-compose.yml` | `qdrant:latest`, `ollama:latest`, unpinned `cloakhq/cloakbrowser` |
