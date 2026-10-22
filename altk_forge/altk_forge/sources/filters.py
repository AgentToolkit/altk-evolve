"""How an observability filter becomes a query on Langfuse.

A filter's criteria, every one optional:
  name {op, value}, from, to (UTC), user_ids, session_ids, environments, tags {op, values}, release, version,
  bookmarked (true/false), conditions [{column, key?, operator, value}] for every other column Langfuse filters on."""

STRING_OPS = ["=", "contains", "does not contain", "starts with", "ends with"]


NUMBER_OPS = [">=", ">", "=", "<", "<="]


OPTION_OPS = ["any of", "none of"]


ARRAY_OPS = ["any of", "all of", "none of"]


BOOL_OPS = ["=", "<>"]


LEVELS = ["DEBUG", "DEFAULT", "WARNING", "ERROR"]


# Columns for the free conditions: column id -> what Langfuse calls it, its filter type and the operators it takes.
COLUMNS = {
    "metadata": {"label": "Metadata (text)", "column": "metadata", "type": "stringObject", "ops": STRING_OPS, "key": "metadata key"},
    "metadata_number": {
        "label": "Metadata (number)",
        "column": "metadata",
        "type": "numberObject",
        "ops": NUMBER_OPS,
        "key": "metadata key",
    },
    "name_text": {"label": "Trace name", "column": "name", "type": "string", "ops": STRING_OPS},
    "user_text": {"label": "User id", "column": "userId", "type": "string", "ops": STRING_OPS},
    "session_text": {"label": "Session id", "column": "sessionId", "type": "string", "ops": STRING_OPS},
    "id": {"label": "Trace id", "column": "id", "type": "stringOptions", "ops": OPTION_OPS, "list": True},
    "latency": {"label": "Latency (s)", "column": "latency", "type": "number", "ops": NUMBER_OPS},
    "inputTokens": {"label": "Input tokens", "column": "inputTokens", "type": "number", "ops": NUMBER_OPS},
    "outputTokens": {"label": "Output tokens", "column": "outputTokens", "type": "number", "ops": NUMBER_OPS},
    "totalTokens": {"label": "Total tokens", "column": "totalTokens", "type": "number", "ops": NUMBER_OPS},
    "inputCost": {"label": "Input cost", "column": "inputCost", "type": "number", "ops": NUMBER_OPS},
    "outputCost": {"label": "Output cost", "column": "outputCost", "type": "number", "ops": NUMBER_OPS},
    "totalCost": {"label": "Total cost", "column": "totalCost", "type": "number", "ops": NUMBER_OPS},
    "level": {
        "label": "Highest observation level",
        "column": "level",
        "type": "stringOptions",
        "ops": OPTION_OPS,
        "list": True,
        "values": LEVELS,
    },
    "errorCount": {"label": "Error observations", "column": "errorCount", "type": "number", "ops": NUMBER_OPS},
    "warningCount": {"label": "Warning observations", "column": "warningCount", "type": "number", "ops": NUMBER_OPS},
    "defaultCount": {"label": "Default observations", "column": "defaultCount", "type": "number", "ops": NUMBER_OPS},
    "debugCount": {"label": "Debug observations", "column": "debugCount", "type": "number", "ops": NUMBER_OPS},
    "scores_avg": {"label": "Score (numeric)", "column": "scores_avg", "type": "numberObject", "ops": NUMBER_OPS, "key": "score name"},
    "score_categories": {
        "label": "Score (category)",
        "column": "score_categories",
        "type": "categoryOptions",
        "ops": OPTION_OPS,
        "key": "score name",
        "list": True,
    },
    "score_booleans": {
        "label": "Score (boolean)",
        "column": "score_booleans",
        "type": "booleanObject",
        "ops": BOOL_OPS,
        "key": "score name",
        "bool": True,
    },
}


class FilterError(ValueError):
    """Criteria that cannot be turned into a query."""


def _list(value):
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value or [] if str(v).strip()]


def clean_criteria(c):
    """The criteria as stored: empty values dropped, lists split, values typed. Raises FilterError."""
    out = {}
    name = c.get("name") or {}
    if (name.get("value") or "").strip():
        if name.get("op", "=") not in STRING_OPS:
            raise FilterError(f"Unknown name operator {name.get('op')!r}")
        out["name"] = {"op": name.get("op", "="), "value": name["value"].strip()}
    for k in ("from", "to"):
        if c.get(k):
            out[k] = c[k]
    if out.get("from") and out.get("to") and out["from"] > out["to"]:
        raise FilterError("'From' is after 'To'")
    if c.get("tz"):
        out["tz"] = c["tz"]
    for k in ("user_ids", "session_ids", "environments"):
        if _list(c.get(k)):
            out[k] = _list(c.get(k))
    tags = c.get("tags") or {}
    if _list(tags.get("values")):
        if tags.get("op", "any of") not in ARRAY_OPS:
            raise FilterError(f"Unknown tags operator {tags.get('op')!r}")
        out["tags"] = {"op": tags.get("op", "any of"), "values": _list(tags.get("values"))}
    for k in ("release", "version"):
        if (c.get(k) or "").strip():
            out[k] = c[k].strip()
    if c.get("bookmarked") in (True, False, "true", "false"):
        out["bookmarked"] = c["bookmarked"] in (True, "true")
    conds = []
    for i, x in enumerate(c.get("conditions") or [], 1):
        spec = COLUMNS.get(x.get("column"))
        if not spec:
            raise FilterError(f"Condition {i}: unknown column {x.get('column')!r}")
        if x.get("operator") not in spec["ops"]:
            raise FilterError(f"Condition {i}: {spec['label']} takes {', '.join(spec['ops'])}")
        if spec.get("key") and not (x.get("key") or "").strip():
            raise FilterError(f"Condition {i}: {spec['label']} needs a {spec['key']}")
        value = x.get("value")
        if spec.get("list"):
            value = _list(value)
            if not value:
                raise FilterError(f"Condition {i}: give at least one value")
        elif spec.get("bool"):
            value = value in (True, "true", "1", 1)
        elif spec["type"] in ("number", "numberObject"):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise FilterError(f"Condition {i}: {spec['label']} needs a number") from None
        elif not str(value or "").strip():
            raise FilterError(f"Condition {i}: give a value")
        conds.append(
            {"column": x["column"], **({"key": x["key"].strip()} if spec.get("key") else {}), "operator": x["operator"], "value": value}
        )
    if conds:
        out["conditions"] = conds
    return out


def langfuse_conditions(criteria, since=None):
    """Criteria as Langfuse's structured filter (a JSON array); `since` adds timestamp >= since."""
    c, out = criteria, []
    if c.get("name"):
        out.append({"type": "string", "column": "name", "operator": c["name"]["op"], "value": c["name"]["value"]})
    if c.get("from"):
        out.append({"type": "datetime", "column": "timestamp", "operator": ">=", "value": c["from"]})
    if c.get("to"):
        out.append({"type": "datetime", "column": "timestamp", "operator": "<=", "value": c["to"]})
    if since:
        out.append({"type": "datetime", "column": "timestamp", "operator": ">=", "value": since})
    for k, column in (("user_ids", "userId"), ("session_ids", "sessionId"), ("environments", "environment")):
        if c.get(k):
            out.append({"type": "stringOptions", "column": column, "operator": "any of", "value": c[k]})
    if c.get("tags"):
        out.append({"type": "arrayOptions", "column": "tags", "operator": c["tags"]["op"], "value": c["tags"]["values"]})
    for k in ("release", "version"):
        if c.get(k):
            out.append({"type": "string", "column": k, "operator": "=", "value": c[k]})
    if "bookmarked" in c:
        out.append({"type": "boolean", "column": "bookmarked", "operator": "=", "value": c["bookmarked"]})
    for x in c.get("conditions") or []:
        spec = COLUMNS[x["column"]]
        out.append(
            {
                "type": spec["type"],
                "column": spec["column"],
                "operator": x["operator"],
                "value": x["value"],
                **({"key": x["key"]} if x.get("key") else {}),
            }
        )
    return out
