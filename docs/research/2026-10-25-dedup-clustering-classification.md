# Jester: decision review of five evidence questions

Access date 2026-10-25. Ten papers, each verified at the link given. **In all ten cases only the abstract was read** (§4), so claims are limited to what each abstract states.

## 1. Evidence table

| Title | Authors | Year | Venue | Link | Method | Key finding | Conf. |
|---|---|---|---|---|---|---|---|
| SemDeDup: Data-efficient learning at web-scale through semantic deduplication | Abbas, Tirumala, Simig, Ganguli, Morcos | 2023 | arXiv | [2303.09540](https://arxiv.org/abs/2303.09540) | k-means on pretrained embeddings + cosine threshold; LAION subset, C4 | Removes 50% of a LAION subset with minimal loss, ~halves training; threshold fixed a priori, validated only by downstream accuracy | Med |
| Is Cosine-Similarity of Embeddings Really About Similarity? | Steck, Ekanadham, Kallus | 2024 | WWW '24 Companion | [10.1145/3589335.3651526](https://doi.org/10.1145/3589335.3651526) | Analytical study, regularized embeddings, extended to deep models | Cosine can yield arbitrary, even non-unique "similarities"; blind thresholding is unjustified | Med |
| PAWS: Paraphrase Adversaries from Word Scrambling | Zhang, Baldridge, He | 2019 | NAACL | [1904.01130](https://arxiv.org/abs/1904.01130) | 108,463 high-lexical-overlap paraphrase / non-paraphrase pairs | SOTA models <40% accuracy; 85% after PAWS training | High |
| Deduplicating Training Data Makes Language Models Better | Lee et al. | 2022 | ACL | [2107.06499](https://arxiv.org/abs/2107.06499) | Exact substring + MinHash near-dup at corpus scale | >1% verbatim output; one 61-word sentence ×60k; 10× less memorization; >4% val contamination | High |
| Density-Based Clustering Based on Hierarchical Density Estimates | Campello, Moulavi, Sander | 2013 | PAKDD | [10.1007/978-3-642-37456-2_14](https://doi.org/10.1007/978-3-642-37456-2_14) | HDBSCAN: density hierarchy + stability-based flat extraction | Stability selects significant clusters without k; beats density-based SOTA on real data | High |
| BERTopic: Neural topic modeling with a class-based TF-IDF procedure | Grootendorst | 2022 | arXiv | [2203.05794](https://arxiv.org/abs/2203.05794) | Embeddings → clustering → c-TF-IDF | Competitive with classical and clustering-based topic models; c-TF-IDF gives cheap labels | Med |
| Snorkel: Rapid Training Data Creation with Weak Supervision | Ratner, Bach, Ehrenberg, Fries, Wu, Ré | 2017 | VLDB | [10.14778/3157794.3157797](https://doi.org/10.14778/3157794.3157797) | Labeling functions + generative label model, no ground truth | 2.8× faster, +45.5% vs 7h hand labeling; +132% over heuristics; within 3.6% of hand-curated | High |
| Retrieval Evaluation with Incomplete Information | Buckley, Voorhees | 2004 | SIGIR | [10.1145/1008992.1009000](https://doi.org/10.1145/1008992.1009000) | Analysis of pooled / incomplete judgments | Standard measures not robust to gross incompleteness; a more robust measure proposed | High |
| Efficient and robust ANN search using HNSW graphs | Malkov, Yashunin | 2016/18 | arXiv (v4) | [1603.09320](https://arxiv.org/abs/1603.09320) | Hierarchical NSW graph index | Logarithmic scaling; neighbour-selection heuristic matters at high recall and on clustered data | High (method) |
| ANN-Benchmarks | Aumüller, Bernhardsson, Faithfull | 2018 | arXiv (full version of SISAP'17) | [1807.05614](https://arxiv.org/abs/1807.05614) | Parameter-grid harness; recall@k vs QPS | Different algorithms reach comparable quality-performance trade-offs; parameters must be swept | High |

## 2. What each question supports

**A. Thresholds.** Supported: a cosine threshold is an operating point, not a property of the model — Steck et al. show cosine can be arbitrary and non-unique; SemDeDup fixes one threshold and justifies it only by downstream accuracy. *Not* supported: that per-category thresholds beat a global one. I found **no peer-reviewed head-to-head study**; searches surfaced only a patent (US20100306204A1, snippet-level, not read) describing per-category duplicate thresholds with no evaluation. So 0.87 and 0.92 are unvalidated conventions, and "per-category" is plausible but unquantified. **Change:** keep the globals; have `dedup-sweep` emit per-category precision–recall curves and adopt a per-category value only where precision at matched recall improves ≥5 points with a CI excluding zero. Also replace per-item round trips with one batched candidate query per nugget (engineering, not literature).

**B. Benchmarks as proxies.** Supported: PAWS is the sharpest proxy for "same pain point, different words" — high lexical overlap, different meaning, where models fall below 40%. QQP is the closest binary duplicate-question proxy; STS/STS-B are graded proxies scored by correlation, with no canonical cut. Lee et al. show corpus-scale "near-duplicate" is operationalised as string overlap, which by construction misses paraphrase. *Not* supported: any benchmark for "same pain point" — identity is asymmetric (one complaint instantiates another), entity- and severity-sensitive, and domain-bound; PAWS pairs are single sentences. **Change:** use PAWS/QQP only as a sanity gate (<0.8 F1 means broken); calibrate on Jester-labelled pairs and never import a threshold.

**C. Clustering.** Supported: HDBSCAN needs no k, models noise explicitly — matching Jester's rule that unclustered nuggets stay eligible — and supplies a stability criterion for selecting clusters without ground truth. BERTopic shows embeddings → cluster → c-TF-IDF is competitive and cheap; c-TF-IDF is a deterministic alternative to a labeller model. *Not* supported: any head-to-head HDBSCAN vs k-means vs agglomerative result on short, noisy comment embeddings — none in the reviewed set. Silhouette and Davies–Bouldin reward convex, equal-variance clusters and punish noise: diagnostics, not truth. **Change:** keep HDBSCAN, tie `min_cluster_size` to the nugget cap, log %noise and stability, sample-audit 30 clusters; do not optimise silhouette.

**D. Rules vs models.** Supported: rules are auditable by construction — the stored matched words *are* the provenance. Snorkel is the strongest hybrid evidence: a model trained on rules-as-labeling-functions lands within 3.6% of hand-curated labels while each rule stays inspectable. On recall, Buckley & Voorhees show incomplete judgment sets bias recall-oriented measures, so recall needs sampling with confidence intervals; capture–recapture with two independent captures (two rule variants, or rules vs model) estimates missed positives. *Not* supported: any published recall measurement for a buyer-signal rule set, and capture–recapture's independence assumption is untestable here. **Change:** keep the rules; add an independently written second variant as capture 2 plus a stratified audit, report recall with a CI, and keep rules as LFs if a model is ever trained.

**E. ANN.** Supported (methodologically only): HNSW scales logarithmically and its neighbour heuristic matters at high recall; ANN-Benchmarks exists because parameter grids must be swept and distinct algorithms converge to similar trade-offs. *Not* supported at abstract level: any concrete M / ef_construction / ef_search numbers at 10^5–10^6. **Change:** treat `ef_search` as the runtime knob and M / `ef_construction` as build cost; measure recall@10 against exact search on Jester's own vectors instead of adopting Qdrant defaults.

## 3. Comparisons

| | Reported performance | Label cost | Auditability | Op. complexity | Fit to local stack |
|---|---|---|---|---|---|
| HDBSCAN | Beats density-based SOTA (2013); no comment-embedding head-to-head found | None; 2 knobs | Medium: assignments + stability, plus a noise label | Low–medium | **Best**: unknown topic count; noise = "still eligible" |
| k-means | Not established in reviewed set | None; needs k | Low–medium: every point forced into a cluster | Low | Good at 10^5; forces outliers into topics |
| Agglomerative | Not established in reviewed set | None; linkage + cut | Medium (dendrogram) | High: O(n²) memory | Poor at 10^5 in full |

| | Reported performance | Label cost | Auditability | Op. complexity | Fit |
|---|---|---|---|---|---|
| Rules only | High precision by construction; recall unmeasured | ~0 | **Full**: matched words stored | Low | **Best today** |
| Supervised only | Within 3.6% of hand labels when trained on LFs | Labels or LFs | Low: attribution ≠ rationale | Medium: training, drift | Weak: breaks reproducibility |
| Hybrid (rules as LFs; or rules + model as capture 2) | +132% over heuristics; ≈ hand-curated (Snorkel) | Low (rules reused) | Partial: LF provenance kept | Medium | Good: yields an estimable recall |

## 4. Not accessible

Full texts were not read for any of the ten; every statement is abstract-level. Blocked/paywalled: Springer chapter for HDBSCAN (IdP redirect; abstract taken from the JCU repository record), ACM DL for Buckley & Voorhees and Steck et al. (bot check; abstracts from NIST and arXiv). PDF extraction was unsupported by the fetch tool. No paper here reports precision/recall vs cosine threshold on comment-like text, and none evaluates per-category thresholds.

## 5. One cheap experiment per question

| Q | Experiment | Metric | Sample | Pass / fail |
|---|---|---|---|---|
| A | `dedup-sweep`, thresholds 0.70–0.95, two categories | Precision at fixed recall; PR-AUC | 200 labelled pairs/category | Per-category beats global 0.87 by ≥5 pts at equal recall, CI excludes 0; else keep global |
| B | PAWS/QQP-tuned vs Jester-tuned threshold, held-out pairs | F1 | 300 pairs (150 pos / 150 hard neg) | Proxy usable if within 5 F1 points; else discard |
| C | Re-cluster 10^5 nuggets: HDBSCAN vs k-means (k from silhouette) | %noise, purity, stability | 30 clusters × 5 items audited | HDBSCAN if noise ≤20% and purity ≥0.80; else k-means |
| D | Stratified audit of rule negatives; second rule variant as capture 2 | Recall ±95% CI | 300 negatives + 100 positives | CI width ≤10 pts; if recall <0.70 expand phrase lists first |
| E | HNSW grid: M∈{16,32,48}, ef_construction∈{100,200,400}, ef_search∈{32,64,128,256} | recall@10 vs exact; p95 latency | 10^5 vectors, 1k queries | recall@10 ≥0.95 at p95 ≤50 ms within RAM; re-check at 10^6 |
