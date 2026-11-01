# Model providers and data egress

What leaves this machine, to whom, and what is kept afterwards. The code this
describes is `python/jester/roles.py` (which provider each job uses),
`python/jester/llm.py` (what each call sends) and
`python/jester/provenance.py` (what Jester records). The privacy side of the
archive itself is in [data-retention.md](data-retention.md); this page is the
egress half of the same story.

## The short version

| Job (`roles.<role>`) | Ships as | Where the call goes | What the prompt carries |
|---|---|---|---|
| `extractor` | `ollama` | `OLLAMA_HOST`, default `http://127.0.0.1:11434` | the comment body, or a JSON batch of up to `extractor_batch_size` comment bodies |
| `synthesizer` | `groq` | `GROQ_BASE_URL`, default `https://api.groq.com/openai/v1` | numbered list of the cluster's nugget insights (no raw comment text) |
| `critic` | `groq` | same as synthesizer | one idea: title, nugget count, problem statement |
| `labeller` | follows the synthesizer | same as synthesizer | the 12 most central members of a cluster, each as its insight plus **raw comment text** (chunked at 700 characters) |
| `embedding` | `ollama` | `OLLAMA_HOST` | the text being embedded (`nomic-embed-text` by default) |
| `chat` | off (`chat_enabled: false`) | as `roles.chat` resolves | the turn's transcript plus any archive rows a tool returned |
| `specifier` | as `roles.specifier` resolves | as resolved | the source page text the proposal is about |

`config/thresholds.yaml` is the shipped answer; `jester models` prints the
resolved table for the current file, and `runs.models_used` records what a
given run actually used.

A provider name is not a host. `fake` never touches the network at all
(`jester run --mock`, `scripts/smoke.sh`, and the test suite use it), and any
`providers.<name>` block with `kind: openai` and a `base_url` points a job at
that server instead of Groq — a local LM Studio, llama.cpp or a gateway.

## Two kinds of "leaves the machine"

**Nothing leaves with the shipped defaults for the extractor and the
embedding model.** Both resolve to Ollama on loopback. The text they carry is
the scraped comment body, which is the most sensitive thing in the archive.

**The synthesizer and critic are configured for Groq on this machine.** They
receive *derived* text — the one-line insight the extractor produced, plus an
idea's title and problem statement — not the raw comment. That is a property
of today's prompt construction (`llm.py`, `_SynthesizerRole.synthesize` /
`_CriticRole.score`), not a guarantee of the schema: a future prompt that
includes `raw_text` changes the answer, and the change should be recorded
here.

**The labeller is the exception, and it follows the synthesizer.** As shipped
it is therefore also Groq, and its prompt carries raw comment text for up to
12 cluster members (`agents/labeller.py` over `Cluster.preview`). If you want
only local egress for comment text, give `roles.labeller` its own
`provider: ollama` block — it exists precisely so that naming a cluster does
not have to share the synthesizer's provider.

## Credentials

| Variable | Read by | Behaviour when missing |
|---|---|---|
| `GROQ_API_KEY` | the built-in `groq` provider (`key_env`) | the call fails with a message naming the variable; no call is attempted |
| `GROQ_BASE_URL` | `GroqClient` | default `https://api.groq.com/openai/v1` |
| `OLLAMA_HOST` | the Ollama client | default loopback host |
| `JESTER_ROLE_<ROLE>` | `roles.resolve_role` | overrides provider/model for one job (`groq:openai/gpt-oss-20b`) |

`providers.<name>.key_env` decides which variable holds a provider's key. A
provider declared without `key_env` sends **no** `Authorization` header, so a
Groq key is never handed to some other server that happens to speak the same
protocol. Keys live in `.env` at the repo root, which is gitignored;
`config/local.env.example` lists the names without values.

## What Jester keeps after a call

`llm_calls` records, per call: role, model, the role digest, SHA-256 of the
prompt and of the reply, their lengths, latency, and whether it failed. The
prompt and reply **text** are stored only when `JESTER_LLM_CALL_TEXT=1`, and
rows are pruned after `JESTER_LLM_CALLS_KEEP_DAYS` days (default 30). So the
default record answers "was this the same question?" without becoming a second
copy of the archive's text.

`jester evidence --run ID` includes those rows; the hashes are what make a
rerun comparable, and the text — when enabled — is retained under the same
30-day window as the calls.

## What the provider keeps

Jester cannot see or delete a provider's copy. **Groq's own privacy policy and
data-processing terms govern what Groq retains after the response** — check
them at groq.com/privacy-policy before sending text you would not paste into a
public issue. Ollama is a process on your own machine, so its retention is
whatever is in your model directory; the prompts are not written to disk by
Jester beyond the opt-in `llm_calls` text above.

There is no data-processing agreement in this repository, and none is implied
by using it. Treat scraped comments as public-but-identifying: they come from
public forums, and a username is often attached.

## Egress that is not a model call

- **Collection.** The signals and real-estate collectors fetch public pages
  from their declared sources (`clickhouse/`, `go/`, `config/signal_rules.yaml`).
  Those are outbound reads, not prompts.
- **Competitor check.** `competitor_search_provider` is `none` as shipped;
  `duckduckgo` would send an idea's query terms to
  `html.duckduckgo.com`, and `fake` keeps it offline.
- **Journal.** `journal snapshot` fetches each cited page and stores it with
  its status, final URL and text — the only journal step that goes to the
  network.
- **Source specs.** `specs.py` fetches a candidate source and its
  `robots.txt` before proposing it.

## Turning it all off

```bash
JESTER_MOCK=1            # the worker and the smoke script pin the stand-ins
python -m jester.cli run --mock
```

`fake` providers make no network call, name themselves in the row they
produce, and are refused where a real model was configured — the batch stays
queued rather than being archived under a stand-in's stamp.

## Reviewing this page

This document is only true as of the shipped `config/thresholds.yaml`. If you
change `roles:` or `providers:`, run `jester models` and update the table
above in the same change; a test cannot read a privacy claim.
