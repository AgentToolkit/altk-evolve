"""The JSON Schema of a trace, and how it compares with the schema a converter handles.

1. JSON hidden in strings is decoded first (`decode`): a string field holding encoded JSON is replaced by the parsed
   value, so its structure belongs to the trace like any other.
2. The JSON Schema of the decoded trace is inferred (`infer_schema`): types, properties, the keys every object had
   (`required`), array items; objects keyed by ids become `additionalProperties`. Values never count, and neither
   does the inside of a payload (PAYLOAD_KEYS: the rows a tool returned, a tool's parameter definitions, call
   arguments): which columns a query returned or which tools were on offer is data, not the shape of the trace.
3. It is compared with the schema of a converter (`compare_schema`, `verdict_of`): same, wider or different."""

import json
import re

MAX_DEPTH = 24


# Keys whose value is a payload: only its type counts, not what is inside. `data`: rows a tool returned (columns differ
# per query); `properties`: a tool's parameter definitions (differ per tool); `args` / `arguments`: a call's arguments.
PAYLOAD_KEYS = {"data", "properties", "args", "arguments"}


# Keys that are data, not field names: ids, hashes, numbers, and keys like "9.60.240.228;db2inst1;bludb" (anything
# starting with a digit or holding ; : / or spaces). Their objects are maps: additionalProperties, not properties.
# Dots alone do not count: OpenTelemetry attribute names are dotted (llm.input_messages).
ID_KEY = re.compile(r"^([0-9a-f]{12,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d.*|.*[;:/\s].*)$", re.I | re.S)


def decode(value, depth=0):
    """The value with every string that holds a JSON object or array replaced by the parsed value, recursively
    (JSON inside JSON inside a string is decoded too)."""
    if depth > MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {k: decode(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v, depth + 1) for v in value]
    if isinstance(value, str):
        s = value.strip()
        if len(s) >= 2 and (s[0], s[-1]) in (("{", "}"), ("[", "]")):
            try:
                return decode(json.loads(s), depth + 1)
            except ValueError:
                return value
    return value


def _type(v):
    return (
        "null"
        if v is None
        else "boolean"
        if isinstance(v, bool)
        else "number"
        if isinstance(v, (int, float))
        else "string"
        if isinstance(v, str)
        else "array"
        if isinstance(v, list)
        else "object"
    )


def _types(node):
    t = (node or {}).get("type", [])
    return set([t] if isinstance(t, str) else t)


def _node(types, **parts):
    types = sorted(types)
    return {"type": types[0] if len(types) == 1 else types, **{k: v for k, v in parts.items() if v is not None}}


def infer_schema(value, depth=0):
    """The JSON Schema of a decoded trace: types, properties, `required` (keys present with a non-null value),
    array `items` (the union of every item), and `additionalProperties` for objects keyed by ids. A payload
    (PAYLOAD_KEYS) is described by its type alone."""
    t = _type(value)
    if depth > MAX_DEPTH or t not in ("object", "array"):
        return {"type": t}
    if t == "array":
        items = None
        for x in value:
            items = merge_schema(items, infer_schema(x, depth + 1))
        return _node({"array"}, items=items)
    props, extra = {}, None
    for k, v in value.items():
        sub = {"type": _type(v)} if k in PAYLOAD_KEYS else infer_schema(v, depth + 1)
        if ID_KEY.match(str(k)) or len(str(k)) > 48:
            extra = merge_schema(extra, sub)
        else:
            props[str(k)] = sub
    required = sorted(k for k in props if value.get(k) is not None)
    return _node({"object"}, properties=dict(sorted(props.items())) or None, required=required or None, additionalProperties=extra)


def merge_schema(a, b):
    """The schema of everything `a` or `b` describes. A key stays required only where every object had it."""
    if a is None or b is None:
        return a if b is None else b
    ta, tb = _types(a), _types(b)
    parts = {}
    if "object" in ta | tb:
        pa, pb = a.get("properties") or {}, b.get("properties") or {}
        props = {k: merge_schema(pa.get(k), pb.get(k)) for k in sorted(set(pa) | set(pb))}
        if "object" in ta and "object" in tb:
            required = sorted(set(a.get("required") or []) & set(b.get("required") or []))
        else:
            required = (a if "object" in ta else b).get("required") or []
        parts.update(
            properties=props or None,
            required=required or None,
            additionalProperties=merge_schema(a.get("additionalProperties"), b.get("additionalProperties")),
        )
    if "array" in ta | tb:
        parts["items"] = merge_schema(a.get("items"), b.get("items"))
    return _node(ta | tb, **parts)


def compare_schema(new, known, path=""):
    """How the schema of a trace differs from the schema a converter handles:
    {added: [...], missing: [...], changed: [...]} (paths like observations[].input.messages[].role).
      added    a property, a type where only null was seen, or array items where arrays were always empty
      missing  a key every earlier trace had is absent: the trace is narrower, which the converter handles as it is
      changed  a value has a type the converter has never seen there"""
    out = {"added": [], "missing": [], "changed": []}
    _compare(new or {}, known or {}, path, out)
    return out


def _compare(n, k, path, out):
    tn, tk = _types(n) - {"null"}, _types(k) - {"null"}
    where = path or "(root)"
    if tn - tk:
        if tk:
            out["changed"].append(f"{where}: was {'|'.join(sorted(tk))}, now {'|'.join(sorted(tn))}")
            return
        out["added"].append(f"{where}: {'|'.join(sorted(tn))}")
    if "object" in tn and "object" in tk:
        pn, pk = n.get("properties") or {}, k.get("properties") or {}
        for key in pn:
            sub = f"{path}.{key}" if path else key
            if key in pk:
                _compare(pn[key], pk[key], sub, out)
            elif _types(pn[key]) - {"null"}:
                out["added"].append(f"{sub}: {'|'.join(sorted(_types(pn[key])))}")
        for key in set(k.get("required") or []) - set(n.get("required") or []):
            out["missing"].append(f"{path}.{key}" if path else key)
        an, ak = n.get("additionalProperties"), k.get("additionalProperties")
        if an and ak:
            _compare(an, ak, f"{path}.*", out)
        elif an:
            out["added"].append(f"{path}.*")
    if "array" in tn and "array" in tk:
        if n.get("items") and k.get("items"):
            _compare(n["items"], k["items"], f"{path}[]", out)
        elif n.get("items"):
            out["added"].append(f"{path}[]")


def verdict_of(diff):
    """same: nothing new (missing keys included: a narrower trace is still the same schema). wider: additions, no
    changed type. different: a value changed type."""
    return "different" if diff["changed"] else "wider" if diff["added"] else "same"


def schema_paths(schema, path=""):
    """Every property path of a schema (for counting and showing)."""
    out = []
    for key, sub in ((schema or {}).get("properties") or {}).items():
        p = f"{path}.{key}" if path else key
        out.append(p)
        out += schema_paths(sub, p)
    if (schema or {}).get("additionalProperties"):
        out += schema_paths(schema["additionalProperties"], f"{path}.*")
    if (schema or {}).get("items"):
        out += schema_paths(schema["items"], f"{path}[]")
    return out
