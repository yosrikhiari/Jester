# Provenance, Reproducibility, and Traceability for Jester

Access date 2026-10-25. Every source was opened and at minimum its abstract read; deviations flagged in §4.

## 1. Sources

| # | Source | Type | Year | Link/DOI | Method or scope | Key finding | Applicability to Jester |
|---|---|---|---|---|---|---|---|
| 1 | W3C PROV family (Overview; Constraints) | Standard | 2013 | [overview](https://www.w3.org/TR/prov-overview/), [constraints](https://www.w3.org/TR/prov-constraints/) | 12-doc normative family: entity/activity/agent, derivation, attribution; formal validity rules | Incubator requirements include reproducibility, versioning, procedures, derivation. Validity = internal consistency, **not truth**; validation RECOMMENDED, not required | Records map 1:1. Needs the relations, not RDF |
| 2 | Stodden, Seiler & Ma | Paper | 2018 | [10.1073/pnas.1708290115](https://doi.org/10.1073/pnas.1708290115) | 204 *Science* (Feb 2011) papers; artifacts requested, replication attempted | **44%** artifacts obtainable, **26%** reproduced; policy "currently insufficient" | Capture artifacts at run time, never post hoc |
| 3 | Pimentel et al. | Paper | 2019 | [10.1109/MSR.2019.00077](https://doi.org/10.1109/MSR.2019.00077) | 1.4M GitHub Jupyter notebooks; execution analysis | Self-documenting ≠ reproducible; most do not re-execute cleanly | Local scripts drift silently |
| 4 | Raff | Paper | 2019 | [10.48550/arXiv.1909.06674](https://doi.org/10.48550/arXiv.1909.06674) | Reimplemented **255** papers, 1984–2017 | Code release "important but **not sufficient**" to determine reproducibility | Code availability ≠ reproducibility |
| 5 | Mitchell et al., Model Cards | Paper | 2019 | [10.1145/3287560.3287596](https://doi.org/10.1145/3287560.3287596) | Documentation framework for released models | Record version, intended use, factors, metrics, eval and training data | Anchor for model+version; prompt/rubric is Jester's extension |
| 6 | Sadasivan et al. | Paper | 2023 | [arXiv:2303.11156](https://arxiv.org/abs/2303.11156) (TMLR) | Stress-tests watermark, zero-shot, neural, retrieval detectors; paraphrase; spoofing | Recursive paraphrase sharply cuts detection with slight quality loss; human text spoofable as AI; AUROC bounded by TV distance | Do not detect. Record — it survives paraphrase |
| 7 | Baethge & Jergas | Paper | 2025 | [10.1186/s41073-025-00173-z](https://doi.org/10.1186/s41073-025-00173-z) | 46 studies, ~32,000 quotations; pre-registered meta-analysis | **16.9%** of quotes wrong (CI 14.1–20.0); **8.0%** major; no trend (p=0.85); impact factor *negatively* associated (ρ=−0.253) | Human error ~1 in 6; anchors are justified |
| 8 | Wadden et al., SciFact | Paper | 2020 | [10.18653/v1/2020.emnlp-main.609](https://doi.org/10.18653/v1/2020.emnlp-main.609) | 1.4K expert claims + abstracts; label **and rationale span** | Verification = evidence selection + span justification, not a bare label | `#accuracy` *is* the rationale-span idea |
| 9 | Kidwell et al. | Paper | 2016 | [10.1371/journal.pbio.1002456](https://doi.org/10.1371/journal.pbio.1002456) | Open-practice badges, *Psychological Science* 2014, vs comparison journals | Data reporting **<3% → 23%** (39% by H1 2015); comparison flat; but "reporting openness does not guarantee openness" | Gates + artifact verification change behavior |
| 10 | Thelwall et al. | Paper | 2013 | [10.1371/journal.pone.0064841](https://doi.org/10.1371/journal.pone.0064841) | 11 altmetrics vs Web of Science citations, 76–208,739 articles | Positive associations except Google+; insufficient for LinkedIn/Pinterest/Q&A/Reddit; Twitter coverage low; window bias can reverse | Readership signals weak-but-real; raw social counts are noise |

## 2. Evidence, gaps, and consequences

**A. Data provenance.** PROV's minimum is a typed entity–activity–agent graph with derivation edges; its stated goals include reproducibility, versioning and representing procedures. It does **not** require RDF/PROV-O, and it makes validation optional while defining validity as internal consistency — not truth or sufficiency. For a derived claim the auditable minimum is the derivation edge plus both endpoint identities. *Consequence:* implement PROV's relations, skip its semantics. Full PROV-O export, bundles, provenance-of-provenance and PROV-AQ endpoints are over-engineering for one operator — **skip**.

**B. Reproducibility.** The dominant failure is not missing code but non-runnable context: 26% reproduction from 44% artifact availability, with code release explicitly insufficient. An environment+commit fingerprint catches **environment drift** (OS/Python/GPU/driver), **code version drift** (commit), and uncommitted edits via the dirty flag. It does **not** catch data drift, nondeterministic kernels (same fingerprint, different numbers), missing steps or execution order, statistical error, or a lockfile changed without a commit. And it is an *identity record, not a control*: it can show two runs differ, never that one is right. *Consequence:* add a per-run **output hash** — the only cheap way to detect same-fingerprint-different-result.

**C. LLM text provenance.** Peer-reviewed work establishes model-level documentation and shows detection is unreliable under paraphrase and spoofable. It does **not** establish that anyone prescribes recording prompt and rubric versions for LLM-assisted writing — I found no such source. That is an absence, not a contradiction. *Consequence:* the triple is a defensible extension of Model Cards logic and is strictly better than watermarking. Do **not** add AI-text detection; it fails exactly where it matters.

**D. Claim-to-evidence traceability.** Claim verification is defined at span level with rationale, and unaided human quotation accuracy is ~83% with 8% major errors and no improvement over time. The evidence does **not** establish an error rate for automated checkers — I could not obtain results tables (§4) and assert none. *Consequence:* keep the deterministic `[value](run:12#metric)` form. Exact-match checking against a stored run is the only checker with zero error by construction; an LLM verifier would inherit §7's error profile plus paraphrase fragility. **Skip it.**

**E. Publication metrics.** Readership/bookmark signals have weak but real association with impact, while several social platforms show none; and behavior changes when a metric is tied to a gate *and* the artifact is verified. No verified study shows post-publication measurement improving subsequent work for a personal blog. *Consequence:* J8 is observation, not a feedback loop — no dashboard. One metric is decision-relevant: did anyone follow a cited evidence link.

## 3. Minimal provenance checklist

**Must-have:** run_id, commit SHA, dirty flag, entrypoint+config, seed · OS, Python, library versions, GPU+driver · input hash or snapshot id with row counts · **output hash** · per claim: span → `run:<id>#<metric>` or source id · per quote: URL, retrieval timestamp, saved-copy hash · plan locked before first run, later edits append-only · per nugget/idea: model id+version, prompt version, rubric version · approval bound to exact version hash.

**Nice-to-have:** container digest / lockfile hash · wall-clock, hardware class, cost · SQLite↔ClickHouse↔CSV reconciliation saved as a run artifact · PROV-O export.

**Skip:** RDF as primary store; a PROV validator; per-sentence provenance; bundles and provenance-of-provenance; PROV-AQ; watermarking; LLM-as-claim-checker; claim confidence scores.

## 4. Not accessible

- **Beaulieu-Jones & Greene 2017**, *Nat. Biotechnol.*, [10.1038/nbt.3780](https://doi.org/10.1038/nbt.3780): DOI resolves, but no abstract is indexed and nature.com blocked retrieval. Excluded rather than characterized from memory.
- **SciFact results tables**: only the ACL abstract page was reachable (PDF unsupported). Task definition verified; **no F1 or error rate asserted**.
- **PNAS / PubMed / Europe PMC / Springer pages**: 403 or redirect loops. Abstracts were obtained via the OpenAlex API, so the abstracts *were* read; only publisher pages were unreachable.
- **PROV evaluation studies**: search surfaced an IEEE micro-provenance climate-workflow paper and a PROV test-case paper; neither retrieved. No claim about what PROV evaluations found.

## 5. J8 — the `measured` slice

**Record**, per published article version, time-boxed to a fixed window (e.g. 28 days) then frozen: version id + content hash, publish timestamp, canonical URL · the run_ids and source ids that version cited, so later behavior attaches to a claim rather than to the post · first-party aggregate only: pageviews, unique visitors, returning share, referrer *class* · **outbound clicks to cited evidence** — the one meaningful signal: did a reader check the evidence · backlinks from technical artifacts, hand-verified · operator-recorded outcome: did this change a decision or idea (binary + link) · the window itself, since mismatched windows can reverse an association.

**Not recorded:** per-reader identifiers, IPs, fingerprints, cross-site or cross-session tracking, third-party pixels — a single operator cannot secure a re-identification surface and it buys nothing decision-relevant · social like/share/follower counts as success (no demonstrated validity; several platforms none at all) · AI-chatbot referrals as ground truth (unstable, non-comparable) · time-on-page or bounce rate (technical readers leaving to read the cited source is success scored as failure) · any composite "engagement score" · revenue/ad metrics if none exist.

If numbers cannot be collected first-party and aggregate, J8 should transition to `measured` with an explicit `no_metrics` outcome rather than add a tracker.
