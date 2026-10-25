"""A strict-subset JSON Schema validator, in stdlib.

Tool arguments crossing from a model into Jester are validated here at the
boundary. The subset is exactly what tool manifests need (and what the
OpenAI "strict" tool schema documents, which is the one interchange shape
every provider in thresholds.yaml already speaks):

    object    {type: 'object', properties: {...}, required: [...],
               additionalProperties: True|False}
    string    {type: 'string', enum: [...], minLength, maxLength}
    number    {type: 'number'|'integer', minimum, maximum}
    boolean   {type: 'boolean'}
    array     {type: 'array', items: {...}, minItems, maxItems}
    null      only as a member of a type LIST, e.g. ['number', 'null']

Everything else — $ref, oneOf, patternProperties, defaults, format, and any
UNKNOWN KEY on a schema — is refused with SchemaError at realisation time,
so a manifest bug is a crash on load, never late-model behaviour. No
constrained decoding exists on the local/urllib path, so validation always
happens in Python at the tool boundary, regardless of provider.

Why not the `jsonschema` package: it validates a subset we define and pin
ourselves, and it would pull `attrs`/`referencing`/`rpds-py` (a Rust wheel)
into a stdlib-first app. Dependency ledger, plans/2026-10-17-autonomous-
curation-research.md §8 — declined.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

_OBJECT_KEYS = {"type", "properties", "required", "additionalProperties", "title"}
_SCALAR_KEYS = {"type", "enum", "minLength", "maxLength", "title"}
_NUMBER_KEYS = {"type", "minimum", "maximum", "title"}
_BOOL_KEYS = {"type", "title"}
_ARRAY_KEYS = {"type", "items", "minItems", "maxItems", "title"}
_TYPES = ("object", "string", "number", "integer", "boolean", "array")


class SchemaError(Exception):
    """The manifest itself is malformed — a programming error, fail fast."""


class ToolArgError(Exception):
    """Model-supplied arguments failed validation at the tool boundary."""


def realize(schema: Dict[str, Any], *, path: str = "") -> Dict[str, Any]:
    """Validate ONE tool manifest schema eagerly; returns it unchanged.

    Called at registry import so that a broken schema is a startup crash,
    not a runtime surprise.
    """
    allowed = _keys_for(schema, path)
    unknown = set(schema) - allowed
    if unknown:
        raise SchemaError(f"{path or '<root>'}: unsupported schema keys "
                          f"{sorted(unknown)} (strict subset only)")
    t = schema.get("type")
    if isinstance(t, list) and t:
        for member in t:
            if member not in _TYPES + ("null",):
                raise SchemaError(f"{path or '<root>'}: unknown type member {member!r}")
        if "null" in t[1:]:
            pass
    elif t in _TYPES:
        pass
    else:
        raise SchemaError(f"{path or '<root>'}: schema needs a known 'type'")
    if "enum" in schema:
        if not isinstance(schema["enum"], (list, tuple)) or not schema["enum"]:
            raise SchemaError(f"{path or '<root>'}: enum must be a non-empty list")
    if t == "object" or (isinstance(t, list) and "object" in t):
        props = schema.get("properties", {})
        if not isinstance(props, dict):
            raise SchemaError(f"{path or '<root>'}: properties must be a mapping")
        for name, sub in props.items():
            realize(sub, path=f"{path}.{name}" if path else name)
        req = schema.get("required", [])
        if not isinstance(req, (list, tuple)):
            raise SchemaError(f"{path or '<root>'}: required must be a list")
        for name in req:
            if name not in props:
                raise SchemaError(f"{path or '<root>'}: required '{name}' has no property")
    if t == "array" or (isinstance(t, list) and "array" in t):
        if "items" in schema:
            realize(schema["items"], path=f"{path}[]")
    for key in ("minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"):
        if key in schema and not isinstance(schema[key], (int, float)) \
                or isinstance(schema.get(key), bool):
            raise SchemaError(f"{path or '<root>'}: {key} must be a number")
    return schema


def _keys_for(schema: Dict[str, Any], path: str) -> set:
    t = schema.get("type")
    if isinstance(t, list):
        # A type list (nullable members) may combine scalar constraints only.
        if set(t) - {"string", "number", "integer", "boolean", "null", "array"}:
            raise SchemaError(f"{path or '<root>'}: nullable lists cover scalars/arrays only")
        merged = set()
        for member in set(t) - {"null"}:
            merged |= {"type", "title", "enum"}
            if member == "string":
                merged |= {"minLength", "maxLength"}
            elif member in ("number", "integer"):
                merged |= {"minimum", "maximum"}
            elif member == "boolean":
                pass
            elif member == "array":
                merged |= {"items", "minItems", "maxItems"}
        return merged
    if t == "object":
        return set(_OBJECT_KEYS)
    if t == "string":
        return set(_SCALAR_KEYS)
    if t in ("number", "integer"):
        return set(_NUMBER_KEYS)
    if t == "boolean":
        return set(_BOOL_KEYS)
    if t == "array":
        return set(_ARRAY_KEYS)
    raise SchemaError(f"{path or '<root>'}: schema needs a known 'type'")


def validate(instance: Any, schema: Dict[str, Any], *, path: str = "") -> None:
    """Validate model-supplied arguments against a realized schema.

    Raises ToolArgError naming the path on the first violation. Never
    raises SchemaError — that was settled at realize() time.
    """
    t = schema.get("type")
    if isinstance(t, list):
        if instance is None and "null" in t:
            return
        last = None
        for member in t:
            if member == "null":
                continue
            try:
                validate(instance, {**schema, "type": member}, path=path)
                return
            except ToolArgError as exc:
                last = exc
        raise last or ToolArgError(f"{path or '<root>'}: invalid value")
    _validate_one(instance, schema, path)


def _validate_one(instance: Any, schema: Dict[str, Any], path: str) -> None:
    where = path or "<root>"
    t = schema.get("type")

    def bad(msg: str) -> ToolArgError:
        return ToolArgError(f"{where}: {msg}")

    if t == "object":
        if not isinstance(instance, dict):
            raise bad(f"expected object, got {_kind(instance)}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = [k for k in instance if k not in props]
            if unknown:
                raise bad(f"unknown argument(s): {', '.join(sorted(unknown))}")
        for name in schema.get("required", ()):
            if name not in instance:
                raise bad(f"missing required argument '{name}'")
        for name, value in instance.items():
            if name in props:
                validate(value, props[name], path=f"{path}.{name}" if path else name)
        return
    if t == "string":
        if not isinstance(instance, str) or isinstance(instance, bool):
            raise bad(f"expected string, got {_kind(instance)}")
        if "enum" in schema and instance not in schema["enum"]:
            raise bad(f"'{instance}' not in enum "
                      f"{', '.join(str(e) for e in schema['enum'][:12])}")
        length = len(instance)
        if "minLength" in schema and length < schema["minLength"]:
            raise bad(f"shorter than {schema['minLength']} characters")
        if "maxLength" in schema and length > schema["maxLength"]:
            raise bad(f"longer than {schema['maxLength']} characters")
        return
    if t in ("number", "integer"):
        if isinstance(instance, bool) or not isinstance(instance, (int, float)):
            raise bad(f"expected {t}, got {_kind(instance)}")
        if t == "integer" and float(instance) != int(instance):
            raise bad(f"expected integer, got non-integer {instance}")
        if "minimum" in schema and instance < schema["minimum"]:
            raise bad(f"below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            raise bad(f"above maximum {schema['maximum']}")
        return
    if t == "boolean":
        if not isinstance(instance, bool):
            raise bad(f"expected boolean, got {_kind(instance)}")
        return
    if t == "array":
        if not isinstance(instance, (list, tuple)):
            raise bad(f"expected array, got {_kind(instance)}")
        items = schema.get("items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise bad(f"more than {schema['maxItems']} item(s)")
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise bad(f"fewer than {schema['minItems']} item(s)")
        if items is not None:
            for i, item in enumerate(instance):
                validate(item, items, path=f"{path}[{i}]" if path else f"[{i}]")
        return
    raise SchemaError(f"{where}: schema has no known 'type'")  # pragma: no cover


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
