"""Model roles: which provider and model each job uses, and how it is called.

Five jobs call a model: the extractor (once per comment), the synthesizer
(once per thread chunk), the critic (once per idea), the labeller (once per
cluster) and the embedding model (once per text). This module is the ONE
place that decides, for each of them, the provider, the model and every
generation setting. The pipeline, the console and the run record all ask
`resolve_role`, so they cannot disagree.

Where a value comes from, highest first:

    1. --role flag or JESTER_ROLE_<ROLE> env var   (provider and model only)
    2. roles.<role>                                 in thresholds.yaml
    3. roles.defaults                               in thresholds.yaml
    4. providers.<name>                             in thresholds.yaml
    5. the old keys: models:, <role>_provider, llm_provider, embedding_*
    6. what Jester did before this module existed   (BUILTIN_* below)

Rung 6 is written down value by value so that a file with no `roles:` block
resolves to exactly the behaviour it had before (plan gate G2). Changing one
of those values is a behaviour change and belongs in its own, measured PR.

A new key, not a reshaped `models:` block, because the Go worker parses the
same file and expects strings under `models:`. A nested block there would
stop the scraper (verified); `roles:` is a key Go simply ignores.
"""
import os
from dataclasses import dataclass, field, fields
from typing import Optional, Tuple

ROLES = ("extractor", "synthesizer", "critic", "labeller", "embedding")
CHAT_ROLES = ROLES[:4]

#: How a provider is spoken to. `openai` is any OpenAI-compatible chat
#: endpoint: Groq is one, and so are a local LM Studio / llama.cpp server or
#: the AgentOps gateway. `fake` is the deterministic stand-in, chosen on purpose.
PROVIDER_KINDS = ("fake", "ollama", "openai")


class RoleError(ValueError):
    """A roles/providers setting that cannot be used. Raised at load time."""


def _think(value):
    """True / False, or a reasoning level. Ollama takes either; an OpenAI-
    compatible server takes the level as `reasoning_effort`."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "on", "yes"):
        return True
    if text in ("false", "off", "no"):
        return False
    if text in ("low", "medium", "high"):
        return text
    raise ValueError(f"{value!r} is not true, false, low, medium or high")


def _bool(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "on", "yes", "1"):
        return True
    if text in ("false", "off", "no", "0"):
        return False
    raise ValueError(f"{value!r} is not true or false")


#: Settings a role, `roles.defaults` or a provider block may carry, with the
#: caster that checks each one and the range it must fall in.
SETTINGS = {
    "temperature": (float, (0.0, 2.0)),
    "top_p": (float, (0.0, 1.0)),
    "top_k": (int, (1, 1000)),
    "presence_penalty": (float, (-2.0, 2.0)),
    "seed": (int, (0, 2**31 - 1)),
    "think": (_think, None),
    "json_mode": (_bool, None),
    "num_ctx": (int, (256, 1_048_576)),
    "keep_alive": (str, None),
    "timeout_s": (float, (1.0, 86_400.0)),
    "max_retries": (int, (0, 10)),
}
ROLE_KEYS = {"provider", "model", "models"} | set(SETTINGS)
PROVIDER_KEYS = {"kind", "host", "base_url", "key_env"} | set(SETTINGS)

#: Providers that exist without being declared. A `providers:` block may
#: override their keys or add new ones beside them.
BUILTIN_PROVIDERS = {
    "fake": {"kind": "fake"},
    # host None: the ollama client reads OLLAMA_HOST, as it always has.
    "ollama": {"kind": "ollama"},
    # base_url None: GroqClient reads GROQ_BASE_URL or Groq's public API.
    "groq": {"kind": "openai", "key_env": "GROQ_API_KEY"},
}

#: What each job did before roles existed, per kind of provider. Ollama roles
#: sent no sampling options at all (the model's own settings applied); the
#: Groq roles sent these temperatures and always asked for a JSON object.
BUILTIN_SETTINGS = {
    ("extractor", "ollama"): {"timeout_s": 180.0},
    ("synthesizer", "ollama"): {"timeout_s": 300.0},
    ("critic", "ollama"): {"timeout_s": 180.0},
    ("labeller", "ollama"): {"timeout_s": 300.0},
    ("embedding", "ollama"): {"timeout_s": 600.0},
    ("extractor", "openai"): {"temperature": 0.2},
    ("synthesizer", "openai"): {"temperature": 0.4},
    ("critic", "openai"): {"temperature": 0.2},
    ("labeller", "openai"): {"temperature": 0.4},
}
BUILTIN_OPENAI = {"timeout_s": 60.0, "json_mode": True, "max_retries": 3}

#: The model a role uses on a built-in provider when nothing names one.
#: A declared provider has no such default: it must be given a model.
BUILTIN_MODELS = {
    ("extractor", "ollama"): "qwen3:8b",
    ("synthesizer", "ollama"): "qwen3:8b",
    ("critic", "ollama"): "qwen3:8b",
    ("labeller", "ollama"): "qwen3:8b",
    ("embedding", "ollama"): "nomic-embed-text",
    ("extractor", "groq"): "openai/gpt-oss-20b",
    ("synthesizer", "groq"): "openai/gpt-oss-120b",
    ("critic", "groq"): "openai/gpt-oss-120b",
    ("labeller", "groq"): "openai/gpt-oss-120b",
}

#: Old-style keys, per role: (provider key, model attribute on Thresholds).
_LEGACY = {
    "extractor": ("extractor_provider", "extractor_model"),
    "synthesizer": ("synthesizer_provider", "synthesizer_model"),
    "critic": ("critic_provider", "critic_model"),
    "embedding": ("embedding_provider", "embedding_model"),
}

ENV_PREFIX = "JESTER_ROLE_"


@dataclass(frozen=True)
class RoleSettings:
    """Everything needed to call one job's model, fully resolved.

    A setting left as None is not sent at all, so the server's own default
    applies. `source` names, per field, the line that set it.
    """

    role: str
    provider: str
    kind: str
    model: str
    models: Tuple[str, ...] = ()        # a jury (plan D8); unused until E3
    host: Optional[str] = None
    base_url: Optional[str] = None
    key_env: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    presence_penalty: Optional[float] = None
    seed: Optional[int] = None
    think: object = None
    json_mode: Optional[bool] = None
    num_ctx: Optional[int] = None
    keep_alive: Optional[str] = None
    timeout_s: Optional[float] = None
    max_retries: Optional[int] = None
    source: dict = field(default_factory=dict, compare=False, hash=False)

    def as_record(self) -> dict:
        """The resolved values, for `runs.models_used` and `jester models`."""
        return {f.name: getattr(self, f.name) for f in fields(self)
                if f.name != "source" and getattr(self, f.name) not in (None, ())}


# ---- parsing and validation ---------------------------------------------------

def _cast_setting(where, key, raw):
    caster, bounds = SETTINGS[key]
    try:
        value = caster(raw)
    except (TypeError, ValueError) as exc:
        raise RoleError(f"{where}.{key}={raw!r}: {exc}") from None
    if bounds and not (bounds[0] <= value <= bounds[1]):
        raise RoleError(f"{where}.{key}={value} out of range [{bounds[0]},{bounds[1]}]")
    return value


def check_blocks(roles, providers) -> None:
    """Shape checks for the two YAML blocks. Unknown keys are refused: a
    misspelt `temprature` that silently did nothing is exactly the kind of
    setting the plan's G6 gate exists to catch."""
    if not isinstance(roles or {}, dict):
        raise RoleError("roles: must be a mapping of role name to settings")
    if not isinstance(providers or {}, dict):
        raise RoleError("providers: must be a mapping of provider name to settings")
    for name, block in (roles or {}).items():
        if name not in ROLES and name != "defaults":
            raise RoleError(f"roles.{name}: not a role; valid: {', '.join(ROLES)}, defaults")
        if not isinstance(block or {}, dict):
            raise RoleError(f"roles.{name}: must be a mapping")
        for key, raw in (block or {}).items():
            if key not in ROLE_KEYS:
                raise RoleError(f"roles.{name}.{key}: unknown setting; valid: "
                                f"{', '.join(sorted(ROLE_KEYS))}")
            if key in SETTINGS:
                _cast_setting(f"roles.{name}", key, raw)
            elif key == "models" and not (isinstance(raw, list)
                                          and all(isinstance(m, str) and m for m in raw)):
                raise RoleError(f"roles.{name}.models: must be a list of model names")
            elif key in ("provider", "model") and not (isinstance(raw, str) and raw.strip()):
                raise RoleError(f"roles.{name}.{key}: must be a non-empty string")
    for name, block in (providers or {}).items():
        if not isinstance(block or {}, dict):
            raise RoleError(f"providers.{name}: must be a mapping")
        for key, raw in (block or {}).items():
            if key not in PROVIDER_KEYS:
                raise RoleError(f"providers.{name}.{key}: unknown setting; valid: "
                                f"{', '.join(sorted(PROVIDER_KEYS))}")
            if key in SETTINGS:
                _cast_setting(f"providers.{name}", key, raw)
        kind = (block or {}).get("kind") or BUILTIN_PROVIDERS.get(name, {}).get("kind")
        if kind not in PROVIDER_KINDS:
            raise RoleError(f"providers.{name}.kind={kind!r}; valid: {', '.join(PROVIDER_KINDS)}")
        if kind == "openai" and name not in BUILTIN_PROVIDERS and not (block or {}).get("base_url"):
            raise RoleError(f"providers.{name}: an openai-kind provider needs base_url")


def parse_override(text: str):
    """`ollama:qwen3:8b` -> ("ollama", "qwen3:8b"); `fake` -> ("fake", None).

    Split on the FIRST colon only: Ollama tags carry their own.
    """
    provider, sep, model = (text or "").strip().partition(":")
    if not provider:
        raise RoleError(f"role override {text!r} is empty; expected provider[:model]")
    return provider.strip(), (model.strip() or None) if sep else None


def env_overrides(env=None) -> dict:
    """{role: (provider, model)} from JESTER_ROLE_<ROLE> variables."""
    env = os.environ if env is None else env
    out = {}
    for role in ROLES:
        raw = env.get(ENV_PREFIX + role.upper(), "").strip()
        if raw:
            out[role] = parse_override(raw)
    return out


# ---- resolution ---------------------------------------------------------------

def _provider_block(cfg, name):
    declared = (getattr(cfg, "providers", None) or {}).get(name)
    if name not in BUILTIN_PROVIDERS and declared is None:
        known = sorted(set(BUILTIN_PROVIDERS) | set(getattr(cfg, "providers", None) or {}))
        raise RoleError(f"provider {name!r} is not defined; known: {', '.join(known)}")
    return {**BUILTIN_PROVIDERS.get(name, {}), **(declared or {})}


def _legacy_provider(cfg, role):
    if role == "embedding":
        return getattr(cfg, "embedding_provider", "fake") or "fake", "embedding_provider"
    key = _LEGACY[role][0]
    override = (getattr(cfg, key, "") or "").strip()
    if override:
        return override, key
    return getattr(cfg, "llm_provider", "fake") or "fake", "llm_provider"


def resolve_role(cfg, role, env=None) -> RoleSettings:
    """The complete settings for one job. See the module docstring for order."""
    if role not in ROLES:
        raise RoleError(f"{role!r} is not a role; valid: {', '.join(ROLES)}")
    roles = getattr(cfg, "roles", None) or {}
    block = roles.get(role) or {}
    defaults = roles.get("defaults") or {}
    override = env_overrides(env).get(role)
    source = {}

    # The labeller names a theme with whatever writes ideas, unless told
    # otherwise: same class of work, and one good model should not need two
    # knobs. Its own block (or an override) breaks the link.
    inherit = role == "labeller" and not override and not (
        block.get("provider") or block.get("model"))
    parent = resolve_role(cfg, "synthesizer", env) if inherit else None

    # provider
    if override:
        provider, src = override[0], "--role / " + ENV_PREFIX + role.upper()
    elif block.get("provider"):
        provider, src = block["provider"], f"roles.{role}.provider"
    elif parent is not None:
        provider, src = parent.provider, "the synthesizer (labeller follows it)"
    elif defaults.get("provider") and role != "embedding":
        provider, src = defaults["provider"], "roles.defaults.provider"
    else:
        provider, src = _legacy_provider(cfg, role)
    provider = provider.strip()
    source["provider"] = src
    pblock = _provider_block(cfg, provider)
    kind = pblock.get("kind")
    if role == "embedding" and kind not in ("fake", "ollama"):
        raise RoleError(f"embedding cannot use {provider!r} ({kind}): only ollama or fake "
                        "serve embeddings")

    # model
    model, msrc = None, None
    if override and override[1]:
        model, msrc = override[1], "--role / " + ENV_PREFIX + role.upper()
    elif block.get("model"):
        model, msrc = block["model"], f"roles.{role}.model"
    elif parent is not None:
        model, msrc = parent.model, "the synthesizer (labeller follows it)"
    elif role in _LEGACY and (getattr(cfg, _LEGACY[role][1], "") or "").strip():
        attr = _LEGACY[role][1]
        model = getattr(cfg, attr).strip()
        msrc = attr if role == "embedding" else f"models.{role}"
    elif (role, provider) in BUILTIN_MODELS:
        model, msrc = BUILTIN_MODELS[(role, provider)], "built-in"
    if kind == "fake":
        model, msrc = model or "", msrc or "n/a (stand-in)"
    elif not model:
        raise RoleError(f"roles.{role}: provider {provider!r} needs a model; "
                        f"set roles.{role}.model")
    source["model"] = msrc

    values = {}
    layers = [
        ("built-in", BUILTIN_OPENAI if kind == "openai" else {}),
        ("built-in", BUILTIN_SETTINGS.get((role, kind), {})),
        (f"providers.{provider}", {k: v for k, v in pblock.items() if k in SETTINGS}),
        ("roles.defaults", {k: v for k, v in defaults.items() if k in SETTINGS}),
        (f"roles.{role}", {k: v for k, v in block.items() if k in SETTINGS}),
    ]
    for where, layer in layers:
        for key, raw in layer.items():
            values[key] = _cast_setting(where, key, raw)
            source[key] = where

    return RoleSettings(
        role=role, provider=provider, kind=kind, model=model,
        models=tuple(block.get("models") or ()),
        host=pblock.get("host") or None,
        base_url=pblock.get("base_url") or None,
        key_env=pblock.get("key_env") if "key_env" in pblock else None,
        source=source, **values,
    )


def resolve_all(cfg, env=None) -> dict:
    return {role: resolve_role(cfg, role, env) for role in ROLES}
