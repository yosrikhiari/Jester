# Config promotion: `config/` and `config-live/`

The repo ships two configuration profiles. `config/` is the default profile
and the only place a default lives; `config-live/` is an **overlay** on it
that switches every model role to the fully local setup. A key the live file
does not carry resolves to the `config/` value, so the two files are *not*
identical and are not meant to be. This page says what each one is for, how a
change moves from one to the other, and how to prove the move did only what
you meant.

## The two profiles

| Directory | What it is | Jobs resolve to |
|---|---|---|
| `config/` | the default profile, tuned for hosted + local mix | extractor `ollama:qwen3:8b`; synthesizer, critic and labeller `groq:openai/gpt-oss-120b`; embedding `ollama:nomic-embed-text`; chat and specifier `fake` |
| `config-live/` | the fully local profile (setup §37.16) | extractor, synthesizer, critic, labeller, chat and specifier `ollama:qwen3:8b`; embedding `ollama:nomic-embed-text` |

### How the overlay is kept honest

`--config config-live` loads that directory on its own. The *intended*
relationship is still an overlay on `config/`: the live file carries every key
it means to change, and anything it leaves out is a documented default. Two
rules follow, and `scripts/check_config_parity.py` enforces both:

- **No live-only key is permitted.** A key in `config-live/thresholds.yaml`
  that `config/` does not carry has no default behind it; add it to `config/`
  first. `llm_provider` is declared in `config/` for exactly this reason.
- **Only declared defaults-only keys may be omitted.** Seventeen keys in
  `config/thresholds.yaml` are deliberately absent from the live profile
  (chat, scheduler rotation, per-platform depth, the dev-only provider
  overrides). That list and a one-line reason per key live in `DEFAULTS_ONLY`
  in `scripts/check_config_parity.py`. Any other `config/` key must be in
  `config-live/` too.

`sources.yaml`, `signal_rules.yaml` and `scraper.yaml` are shared verbatim.

The console enforces the write half of the rule directly: saving thresholds
**without** a profile mirrors the write into both `config/thresholds.yaml` and
`config-live/thresholds.yaml`, while choosing a profile writes only that
profile. Model choice is never mirrored. (The mirror applies only when the
console serves the repo's own `config/`; pointed at any other directory, it
writes exactly there.)

Two shipped facts that surprise people:

- **`DEFAULT_CONFIG_DIR` is the repo's `config/`, not the current directory.**
  `python -m jester.cli run` from anywhere reads `config/`. Other directories
  need `--config`.
- **The Docker stack does not use either profile as-is.** `jester-console`
  bind-mounts `docker/config/thresholds.yaml` over `/app/config/thresholds.yaml`
  read-only, with every provider forced to `fake`, so `docker compose up`
  runs offline with no keys. That override is the bind mount in
  `docker-compose.yml`; removing the mount is what turns the container onto
  the on-disk profile. Your real `config/thresholds.yaml` is never modified.

## What you actually edit

Model choice lives in two places per job, and the resolver reads both:

```yaml
# config/thresholds.yaml
synthesizer_provider: groq          # the provider for one job
models:                             # per-job model names
  synthesizer: openai/gpt-oss-120b
```

`jester models` prints the resolved provider, model and settings for every
job **and the line that set each value**, so "why is the critic on Ollama?"
has an answer that is not "read the resolver":

```bash
python -m jester.cli models
python -m jester.cli models --diff config-live
```

`--diff` prints only the keys that resolve differently between the two
directories, one line each, then `the two profiles resolve every role
identically` if there are none. It also prints `unused:` lines for config keys
no code reads. The shipped profiles carry none (S-13); a key added by hand is
reported the moment `jester models` loads the file.

`--diff` compares **role records only**. Thresholds, prefilter rules and
source lists are outside it; the key sets are checked by the parity gate
below, and the values by:

```bash
git diff --no-index config/thresholds.yaml config-live/thresholds.yaml
```

## The parity gate

`scripts/check_config_parity.py` is the machine version of the two overlay
rules. It fails when `config-live/` has a key `config/` does not, or when a
`config/` key is missing from `config-live/` without a `DEFAULTS_ONLY` entry,
naming every offender; on success it prints the counts and exits 0. The
Python CI job runs it after the lock install, since it needs PyYAML.

```bash
python scripts/check_config_parity.py
# config parity: 36 key(s) in config/, 19 in config-live/, 17 allowlisted defaults-only
```

It compares **which keys exist**, not their values: a deliberate live override
of a shared key (`competitor_search_provider: duckduckgo`) passes. To make a
new defaults-only key legal, add it to `DEFAULTS_ONLY` with the reason in the
same change; to promote one, add it to `config-live/` and remove the entry.

## Promoting a change

"Promotion" means moving an intended model change from the profile you tuned
to the other one. There is no promotion script and no state to migrate — each
profile is read at startup — so the discipline is in the verification, not the
copy; the parity gate is what checks the result.

1. **Edit the source profile.** For example, switch the synthesizer in
   `config/thresholds.yaml` from Groq to local Ollama.
2. **Mirror the model block** into the other profile by hand. Leave the
   non-model keys alone: if one differs, that is either a deliberate live
   override or drift, and the parity gate below tells you whether the key set
   is legal while the `git diff` shows the values.
3. **Prove the difference is exactly what you meant:**

   ```bash
   python -m jester.cli models --diff config-live
   ```

   Every printed row should be one you intended. A row you did not intend
   means the mirroring missed or over-reached.
4. **Prove the shared half still agrees:**

   ```bash
   python scripts/check_config_parity.py
   ```

   It must print the counts and exit 0. A key it names is either a missed
   promotion or a new defaults-only key that needs a reason in
   `DEFAULTS_ONLY`.
5. **Exercise the pipeline on the new profile:** `python -m jester.cli smoke`,
   then one small real run, and read `python -m jester.cli runs` for the flags
   and the `models_used` record.

Validation runs at load: a value outside its documented range raises
`ConfigError` and the process refuses to start, so a bad edit fails at the
first command rather than at 3am mid-cycle.

## Overrides: three rungs, in order

A per-run override does not edit either profile — it is recorded instead:

1. **Environment.** `.env` is loaded by `python/jester/env.py`. A value
   already exported in the shell wins, including an explicitly empty one (that
   is how `JESTER_QDRANT_URL=` selects per-database local vector mode).
2. **Per-run flag.** `--role critic=ollama:qwen3:8b` on `run`, `treat`,
   `cycle`, `cluster`, `resynth` or `rescore` sets `JESTER_ROLE_CRITIC` for
   that process only.
3. **Console card.** **Agent models** edits the same `roles:` block, profile
   by profile, with a **test** button that makes one real call per job.

All of these land in the run row's `models_used.overrides` /
`models_used.roles`, so a score shift can be traced to the settings that
produced it. The console writes one profile at a time: either a profile is
named explicitly, or the shared keys are mirrored into both.

## Rollback

Both directories are ordinary tracked files. `git checkout -- config/` (or
`config-live/`) restores the last committed profile; nothing derives state
from the config except the run's own `models_used` record, which is meant to
keep saying what actually ran even after the config moved on.
