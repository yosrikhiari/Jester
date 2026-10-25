# Evidence review for Jester (accessed 2026-10-25)

Only papers that could change a decision. All 10 verified by DOI, ACL Anthology ID, or arXiv ID; abstracts read for all, full text for Gilardi, Zheng, Cheng. † = abstract only.

## 1. Papers

| Title | Authors | Year | Venue | DOI / link | Method | Key finding | Confidence |
|---|---|---|---|---|---|---|---|
| ChatGPT outperforms crowd-workers for text-annotation tasks | Gilardi, Alizadeh, Kubli | 2023 | PNAS | [10.1073/pnas.2305016120](https://doi.org/10.1073/pnas.2305016120) | 6,183 tweets/news; 4 codebook tasks; ChatGPT vs MTurk vs 3 trained coders | +25pp accuracy over MTurk; run-to-run agreement 91% (T=1) / 97% (T=0.2) vs 79% trained, 56% MTurk; accuracy vs humans only 59–83% | Peer-reviewed |
| Hybrid intelligence QDA coding | Boettinger, Bürgel, Bartsch | 2026 | Quality & Quantity | [10.1007/s11135-026-03036-x](https://doi.org/10.1007/s11135-026-03036-x) | 164 interviews (752k words); manual vs AI coding, 2 prompt conditions | Agreement low; relevant segments mostly found, but substantial **overcoding** in count and length; AI also found segments humans missed; replacement rejected | Peer-reviewed |
| Judging LLM-as-a-Judge | Zheng et al. | 2023 | NeurIPS D&B | [arXiv:2306.05685](https://arxiv.org/abs/2306.05685) | 80 questions; 3k expert + 3k crowd votes; GPT-4 vs 58 experts | Position bias (GPT-4 consistent 65%, Claude-v1 23.8%); verbosity attack fails 91.3% (GPT-3.5) vs 8.7% (GPT-4); **1–10 single-answer grading ≈ pairwise**: 85% human agreement (S2), 60% counting ties (S1) | Peer-reviewed |
| Towards Understanding Sycophancy in LMs | Sharma et al. | 2024 | ICLR | [arXiv:2310.13548](https://arxiv.org/abs/2310.13548) | 5 assistants × 4 free-form tasks; preference data | All five sycophantic; humans and preference models prefer convincing sycophantic answers over correct ones sometimes | Peer-reviewed |
| Prometheus 2 | Kim et al. | 2024 | EMNLP | [10.18653/v1/2024.emnlp-main.248](https://doi.org/10.18653/v1/2024.emnlp-main.248) | Open evaluator LM; 4 direct-assessment + 4 pairwise benchmarks | Supports user-defined **rubric direct assessment**; best human correlation *among open evaluators* | Peer-reviewed † |
| Can LLMs generate novel research ideas? | Si, Yang, Hashimoto | 2024 | arXiv | [arXiv:2409.04109](https://arxiv.org/abs/2409.04109) | 100+ NLP researchers; 49 LLM vs 49 human ideas; blind review | LLM ideas more novel (p<0.05), slightly weaker feasibility; LLM self-evaluation fails; low diversity | Preprint |
| Text generation: systematic literature review | Becker, Wahle, Gipp, Ruas | 2026 | JAIR 86 | [10.1613/jair.1.22258](https://doi.org/10.1613/jair.1.22258) | SLR of 257 papers, 2017–2025 | Evaluation split model-free / model-based / human; no task-agnostic metric; open-ended generation hardest | Peer-reviewed † |
| Batch Prompting | Cheng, Kasai, Yu | 2023 | EMNLP Industry | [10.18653/v1/2023.emnlp-industry.74](https://doi.org/10.18653/v1/2023.emnlp-industry.74) | 10 QA/NLI datasets; Codex, GPT-3.5, GPT-4; b=2–6 | Up to 5× token savings; comparable quality at small b; **sharp drop at b=6 in 4/5 datasets**; worse with longer inputs | Peer-reviewed |
| How is ChatGPT's behavior changing over time? | Chen, Zaharia, Zou | 2023 | arXiv | [arXiv:2307.09009](https://arxiv.org/abs/2307.09009) | Same prompts, Mar vs Jun 2023; 7 task families | GPT-4 prime/composite 84%→51%; instruction-following fell; continuous monitoring advised | Preprint |
| Lessons from the trenches on reproducible eval | Biderman et al. | 2024 | arXiv (v3 2026) | [arXiv:2405.14782](https://arxiv.org/abs/2405.14782) | 3 years of lm-eval harness practice | Results highly setup-sensitive; record exact model revision, prompt, seeds, harness version | Preprint † |

## 2. What the evidence supports

**A. Extraction reliability.**
*Supports.* Codebook-driven closed-set classification beats crowd workers at ~1/30 the cost, with higher run-to-run consistency than trained coders (Gilardi). Over-extraction is measured, not hypothetical: more segments, longer segments, low agreement (Boettinger).
*Does not support.* Transfer to 8B local models — all evidence is GPT-3.5/4-class. High kappa: Gilardi reports percent agreement, and 97% is run-to-run consistency, not human agreement (accuracy 59–83%). "One comment → one nugget" — no study tests single-item extraction from forum comments; overcoding is not fabrication.
*Decision.* Keep `b=1`. Cap nuggets per comment and segment length. Report Cohen's κ against human labels, not percent agreement. Measure triviality-filter false negatives separately: discarded comments are unrecoverable.

**B. LLM-as-judge.**
*Supports.* Position and verbosity bias are strong and reproducible; swapping order and reference answers mitigate them. Sycophancy is documented across five assistants. Critically, **direct 1–10 grading tracks human judgment about as well as pairwise does** (Zheng), and rubric-conditioned direct assessment is supported (Prometheus 2).
*Does not support.* Absolute calibration — agreement drops 85%→60% once ties count. That self-preference is established: Zheng reports GPT-4 +10%, Claude +25% own-output win rate but says their design *cannot* determine self-enhancement. Nothing validates 1–10 scoring of product-idea dimensions, and no evidence covers 8B judges.
*Decision.* Keep the rubric but anchor it (2/5/8 descriptors) and randomise dimension order; never compare scores across model versions. Keep `competition=null`; the eval's imputation of missing competition as 5 converts "unknown" into "average" — fix the benchmark. Test a different critic model rather than assuming self-preference.

**C. Evaluation methodology.**
*Supports.* Blinded expert review with a statistical test is best practice for idea quality (Si); no single metric generalises, so evaluation must be task-specific and multi-method (Becker).
*Does not support.* That automatic scores track quality for open-ended ideas — Si found LLM self-evaluation fails, and novelty came with weaker feasibility, so one "quality" number conflates them.
*Decision.* Keep known-good/known-bad sets but score them as blinded pairwise (does the pipeline rank good above bad?), with a feasibility sub-metric. Report rank correlation, not pass/fail counts.

**D. Batching.**
*Supports.* b=3–4 is near-free on short verifiable-answer tasks; cost falls near-linearly.
*Does not support.* b=5–20. Tested range was 2–6; accuracy degraded with b, sharply at 6, and worse with longer inputs. No study batches multi-field extraction from free text. b=20 is extrapolation.
*Decision.* Restrict the eval to b∈{1,3,5}; score per-item F1 and fabricated-nugget count, not aggregate accuracy. Throughput gains are real but do not license quality claims at b=20.

**E. Reproducibility.**
*Supports.* Run-to-run variance at fixed settings (Gilardi 91% vs 97% between temperatures 1 and 0.2); hosted models drift over weeks (Chen 84%→51%); results are setup-sensitive (Biderman).
*Does not support.* That a seed guarantees determinism — none shows that. Drift is vendor-side: it applies to Groq and to re-pulled mutable Ollama tags, not fixed local weights.
*Decision.* Record model digest (not tag), quantisation, context size, temperature, seed, prompt hash, rubric version, commit — add digest and sampling parameters to what Jester already logs. Report n=3 runs with spread. Pin Groq snapshot dates.

## 3. Papers you should not rely on (as usually cited)

- **Gilardi** as "LLMs beat humans at annotation": closed-set classification with a supplied codebook, gpt-3.5-turbo, 59–83% accuracy against the human gold standard.
- **Zheng** as "judges agree with humans >80%": GPT-4, chat preference, non-tie votes only. Not rubric scoring of product ideas.
- **Cheng** as "batching is free": short QA, single-token answers, b≤6 — the paper itself reports degradation with batch size.
- **Si** as "LLM ideas are more novel": preprint, research ideas, novelty bought with weaker feasibility; expert novelty judgments are themselves unreliable.
- **Prometheus 2** as "open judges match humans": best among open evaluators, not at parity.
- **Chen** as evidence that Ollama models drift: the drift was vendor-side; pinned local weights are a different regime.

## 4. Not accessible

- **Nature Sci. Reports 2025** annotator-benchmark table: institutional IdP redirect. Not used.
- **Törnberg, Soc. Sci. Computer Rev.** (10.1177/08944393241286471): DOI and Crossref abstract verified (GPT-4 accuracy 0.934, reliability 0.982, 11 countries), full text 403 — dropped for space, not quality.
- **Springer HTML** for Boettinger: IdP redirect; used the Crossref abstract.
- **Biderman** publication status: dblp/Anthology bot-blocked; treated as preprint.
- Abstract-only: Chen, Biderman, Prometheus 2, Becker, Si.

## 5. One-day experiments

| Q | Protocol | Metric | Pass/fail |
|---|---|---|---|
| A | 100 comments stratified by source; label nuggets blind, re-label after 24h, adjudicate | nugget F1 per comment; nuggets/comment vs human; fabricated-nugget rate (no supporting span) | F1 ≥ 0.70, ratio ≤ 1.2, fabrication ≤ 5% |
| B | 24 ideas × 3 repeats × 2 judges (same/different model), random dimension order | SD across repeats; Spearman vs blinded human ranking | SD ≤ 1.0 and ρ ≥ 0.7; switch judge if same-model demand runs >1 point high |
| C | 10 good + 10 bad ideas; 3 perturbations (shuffle nuggets, drop 20%, paraphrase 10%) | AUC good-vs-bad; rank correlation across perturbations | AUC ≥ 0.85 and ρ ≥ 0.8 |
| D | 200 comments at b=1/3/5/10, fixed temperature and prompt | F1 vs b=1; Δ fabricated nuggets; tokens | F1 ≥ 0.95, Δfabrication ≤ 1pp; expect b=10 to fail |
| E | 30 comments × 5 runs at temp 0, ×5 at 0.8 | mean pairwise Jaccard of nugget sets | ≥ 0.90 at temp 0; below 0.80, no single-run numbers may be reported |
