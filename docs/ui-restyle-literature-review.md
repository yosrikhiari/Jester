# Literature review: Jester console restyle

Sources accessed 2026-10-25. Findings are from abstracts; full texts were paywalled.

| Title | Authors | Year | Venue | DOI | Method | Key finding | Applicability |
|---|---|---|---|---|---|---|---|
| What is beautiful is usable | Tractinsky, Katz, Ikar | 2000 | Interacting with Computers 13(2) | [10.1016/S0953-5438(00)00031-X](https://doi.org/10.1016/S0953-5438(00)00031-X) | Lab exp, ATM surrogate, pre/post ratings, MANCOVA | Aesthetics tracked perceived usability pre- and post-use; actual usability did not shift aesthetic ratings | Buys perceived usability even if scanning speed stays flat |
| Design aesthetics in usability testing | Sonderegger & Sauer | 2010 | Applied Ergonomics 41(3) | [10.1016/j.apergo.2009.09.002](https://doi.org/10.1016/j.apergo.2009.09.002) | Between-subjects, n=60 (14-17y), identical phone shells | High appeal raised perceived usability and shortened task times | Best case for the branch, but adolescents on phones |
| Product aesthetics and user state | Sauer & Sonderegger | 2010 | Behaviour & IT | [10.1080/0144929X.2010.503352](https://doi.org/10.1080/0144929X.2010.503352) | Between-subjects, n=60, aesthetics x prior usage event | High aesthetics raised perceived usability but lengthened task times | Prettier can mean slower; direction unstable |
| Old wine in new bottles | Bargas-Avila & Hornbæk | 2011 | CHI | [10.1145/1978942.1979336](https://doi.org/10.1145/1978942.1979336) | Review, 51 publications / 66 studies | Aesthetics most-assessed; many home-made questionnaires unvalidated | Discount the beauty-to-usability effect sizes |
| Formatting of alphanumeric displays | Tullis | 1983 | Human Factors 25(6) | [10.1177/001872088302500604](https://doi.org/10.1177/001872088302500604) | Review plus objective format metrics | Overall density, local density, grouping, layout complexity each predicted usability | Density is tuned, not minimised |
| Minimalism in information visualization | Inbar, Tractinsky, Meyer | 2007 | ECCE | [10.1145/1362550.1362587](https://doi.org/10.1145/1362550.1362587) | Acceptance study of minimalist displays | Treats maximal data-ink as an open question, not a rule (1-sentence abstract only) | No data-ink basis for stripping chrome |
| Text-background polarity and performance | Buchner & Baumgartner | 2007 | Ergonomics 50(7) | [10.1080/00140130701306413](https://doi.org/10.1080/00140130701306413) | Proofreading experiments plus physiological measures | Dark-on-light better regardless of ambient light or chromaticity; hue cannot offset low luminance contrast | Strongest support for light default; no hue-only coding |
| Typeface, size and polarity on glance legibility | Dobres, Chahine, Reimer, Gould, Mehler, Coughlin | 2016 | Ergonomics | [10.1080/00140139.2015.1137637](https://doi.org/10.1080/00140139.2015.1137637) | Adaptive-staircase lexical decision; typeface x polarity x size x age | Humanist beat square grotesque; black-on-white beat white-on-black | Humanist sans on light is defensible |
| Web page design: depth vs breadth | Larson & Czerwinski | 1998 | CHI | [10.1145/274644.274649](https://doi.org/10.1145/274644.274649) | Controlled experiment with scent | Depth did not harm search; medium breadth beat broadest-shallowest | 13-item side nav sits well inside range |
| Hidden costs of GUIs | Lane, Napier, Peres, Sandor | 2005 | IJHCI 18(2) | [10.1207/s15327590ijhc1802_1](https://doi.org/10.1207/s15327590ijhc1802_1) | Survey of 251 experts plus timed study | Experts kept menus even for most frequent commands | Palette will be under-used; nav stays primary |
| Progressive disclosure | Springer & Whittaker | 2020 | ACM TOCHI | [10.1145/3374218](https://doi.org/10.1145/3374218) | Four studies, analytics system | Disclosure aided model-building but hurt accuracy perceptions when it surfaced errors | One expert should default to seeing data |
| Color coding research for visual displays | Christ | 1975 | Human Factors 17(6) | [10.1177/001872087501700602](https://doi.org/10.1177/001872087501700602) | Quantitative review, 42 studies 1952-73 | Colour effective or detrimental depending on conditions | Status needs text/icon redundancy |

Normative reference, not a paper: [WCAG 2.2](https://www.w3.org/TR/WCAG22/), W3C Recommendation, 5 Oct 2023. SC 2.5.8 Target Size (Minimum), AA, is 24x24 CSS px, with an explicit "Essential" exception for "interactive data visualization where targets are necessarily dense", and a recommendation to offer a density setting.

## Do these findings support or challenge the branch?

**Density/whitespace - partly challenged.** Nothing found shows whitespace helps expert table scanning. Tullis treats overall density, local density, grouping and layout complexity as separate predictors; low density is not automatically better. Inbar removes the aesthetic case for minimalism without supplying a performance case either way. Cut chrome freely, but not rows-per-screen on Queue, Posts or Runs; keep rows near 28-32px, not 48px.

**Light-vs-dark default - supported.** Two independent labs, consistent direction: dark-on-light wins on proofreading and threshold legibility, and survives ambient-light and chromaticity manipulation. The "dark mode reduces long-session fatigue" claim has no comparable support. Keep a dark toggle anyway; preference is real even where performance is unproven.

**Typography - partly supported.** Dobres et al. give a real humanist-over-grotesque legibility effect, backing Lexend on light. Tabular figures and 40-48px headings are unsupported: a search for tabular-figure evidence turned up nothing peer-reviewed, so it stays right for column scanning but must be argued as convention. Big headings are aesthetic - Tractinsky says that reads as more usable, nothing says it helps performance - and a 48px title plus padding spends ~120px per page, one table row on a 1366x768 laptop.

**Navigation - supported.** Larson & Czerwinski show depth is cheap and medium breadth beats maximal breadth, so 13 side-nav items are comfortable. Lane et al. argue against betting on a palette: experts with menu knowledge do not migrate. I found no peer-reviewed side-vs-top-nav study, so that part is convention.

**Color/status - the thin part.** Christ's review is the caution: colour helps when it is the searched dimension and hurts when it competes. One accent works for selection, focus and links, but status needs a reserved semantic set, always paired with text or icon: colour-only coding is conditionally harmful and ~1 in 12 men is red-green deficient. 0.8px borders on off-white need a contrast pass against SC 1.4.11 (3:1) or card edges vanish.

## Validation experiments worth running

1. **Time-to-answer benchmark.** 10 canned questions per heavy page; old vs new, 3 counterbalanced sessions; median time and errors. n=1 works with repeated measures - report per-question deltas.
2. **Rows-above-fold audit.** Count primary rows visible at 1366x768 and 1920x1080 on Runs, Queue, Posts; require no regression. Cheapest guard against the 48px-title risk.
3. **Contrast/token lint.** Script every token pair; assert 4.5:1 body, 3:1 non-text and focus rings; fail the build.
4. **Keyboard-only pass.** Tab all 13 pages; focus visible and unobscured (SC 2.4.11), order matches visual order, no traps; Playwright per commit.
5. **Two-week theme A/B.** Light fortnight vs dark in the journal; log per-session errors and 1-5 eyestrain; compare medians.
6. **Palette telemetry.** Opens vs commands executed; under ~10% of navigations after 4 weeks, stop investing.
7. **Perceived-vs-actual split.** SUS before and after alongside experiment 1. If SUS rises while timings do not, you measured the Tractinsky effect, not productivity.

## Evidence quality and limitations

Samples are small (n=24-60), single-session, lab-based and mostly students; Sonderegger's were 14-17-year-olds, a poor proxy for an expert operator. The performance direction is not robust: Sonderegger & Sauer found aesthetics sped tasks up, Sauer & Sonderegger found they slowed them down. Tractinsky 2000 is one ATM-surrogate study, heavily cited but rarely replicated in professional tools. Polarity replicates best but was tested on proofreading, not monitoring; Christ (1975) and Tullis (1983) predate modern displays. For tabular figures, 40-48px headings, 220px nav width, 0.8px borders and 120ms motion I found no peer-reviewed evidence at all - argue these on taste and constraint, not citation.

## Not accessible

- **Kurosu & Kashimura (1995), "Apparent usability vs. inherent usability", CHI '95**, doi [10.1145/223355.223680](https://doi.org/10.1145/223355.223680) - the ACM record carries no abstract, so it is excluded from the table. Its companion IEEE SMC record (doi 10.1109/ISMC.1995.537986) was readable and reports low correlations between apparent and inherent usability; the CHI paper's own results stay unverified.
- **Inbar, Tractinsky & Meyer (2007)** - only a one-sentence extended abstract was retrievable; full results unverified.
- **ScienceDirect and ACM DL full texts** returned HTTP 403 on every attempt; all entries rest on abstracts.
