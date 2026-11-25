"""Observability filters: which traces interest us, for one kind of source (langfuse). A filter does not name a
connection: any number of pipelines use it with any number of connections. Stored in .data/filters.json."""

import json

from ..core.clock import now, utc
from ..core.errors import InputError
from ..core.ids import unique_id
from ..core.paths import DATA
from ..core.store import LOCK, read_json, write_json
from ..sources.filters import FilterError, clean_criteria, langfuse_conditions
from ..sources.http import SourceError
from ..sources.langfuse import Langfuse
from .integrations import client_for, get_integration
from .kinds import FILTER_KINDS, KINDS
from .state import all_pipelines, reset_watermarks

FILTERS = DATA / "filters.json"


def all_filters():
    return read_json(FILTERS, [])


def get_filter(filter_id):
    f = next((x for x in all_filters() if x["id"] == filter_id), None)
    if not f:
        raise InputError(f"No filter {filter_id}")
    return f


def list_filters():
    pipes = all_pipelines()
    return [
        {**f, "used_by": [p["id"] for p in pipes if any(f["id"] in i.get("filter_ids", []) for i in p.get("inputs", []))]}
        for f in all_filters()
    ]


def clean_filter_criteria(kind, criteria):
    if kind not in FILTER_KINDS:
        raise InputError(f"Unknown filter type {kind!r}")
    c = dict(criteria or {})
    c["from"], c["to"] = utc(c.get("from")), utc(c.get("to"), end_of_day=True)
    try:
        return clean_criteria(c)
    except FilterError as e:
        raise InputError(str(e)) from None


def save_filter(data, filter_id=None):
    kind = data.get("kind") or "langfuse"
    name = (data.get("name") or "").strip()
    if not name:
        raise InputError("Missing name")
    criteria = clean_filter_criteria(kind, data.get("criteria"))
    with LOCK:
        items = all_filters()
        current = next((x for x in items if x["id"] == filter_id), None) if filter_id else None
        if filter_id and not current:
            raise InputError(f"No filter {filter_id}")
        item = dict(current or {"id": unique_id(name, {x["id"] for x in items}), "created_at": now()})
        changed = current and current.get("criteria") != criteria
        item.update(kind=kind, name=name, description=(data.get("description") or "").strip() or None, criteria=criteria, updated_at=now())
        items = [item if x["id"] == item["id"] else x for x in items] if current else items + [item]
        write_json(FILTERS, items)
    if changed:  # a wider filter may select traces older than the watermarks
        for p in all_pipelines():
            if any(item["id"] in i.get("filter_ids", []) for i in p.get("inputs", [])):
                reset_watermarks(p["id"])
    return item


def delete_filter(filter_id):
    with LOCK:
        get_filter(filter_id)
        users = [p["id"] for p in all_pipelines() if any(filter_id in i.get("filter_ids", []) for i in p.get("inputs", []))]
        if users:
            raise InputError(f"Used by pipeline {', '.join(users)}; change or delete those first")
        write_json(FILTERS, [x for x in all_filters() if x["id"] != filter_id])


def matching_traces(integration_id, filter_ids, since=None, limit=None):
    """Traces of one connection that any of the filters selects (deduplicated), oldest first."""
    integ = get_integration(integration_id)
    if not integ:
        raise InputError(f"No integration {integration_id}")
    k = KINDS[integ["kind"]]
    if k["direction"] != "input" or not k.get("import"):
        raise InputError(f"Import from {k['label']} is not supported")
    client = client_for(integration_id)
    if not k.get("filter_kind"):  # a folder: every file, no filters
        return [{**t, "filters": []} for t in client.list_traces(since, limit=limit)]
    seen = {}
    for fid in filter_ids:
        f = get_filter(fid)
        if f["kind"] != k["filter_kind"]:
            raise InputError(f"Filter {f['name']} is for {f['kind']}, not {k['label']}")
        for t in client.list_traces(langfuse_conditions(f["criteria"], since), limit=limit):
            seen.setdefault(t["id"], {**t, "filters": []})["filters"].append(fid)
    return sorted(seen.values(), key=lambda t: t["timestamp"] or "")


def preview(integration_id, filter_ids=None, criteria=None, kind="langfuse", sample=5):
    """How many traces a connection and saved filters (or unsaved criteria) select right now."""
    integ = get_integration(integration_id)
    if not integ:
        raise InputError("Choose a connection")
    try:
        if criteria is not None:
            conds = langfuse_conditions(clean_filter_criteria(kind, criteria))
            client = client_for(integration_id)
            if not isinstance(client, Langfuse):
                raise InputError(f"{KINDS[integ['kind']]['label']} cannot be filtered")
            data = client.get(
                "traces", page=1, limit=sample, orderBy="timestamp.desc", fields="core", filter=json.dumps(conds) if conds else None
            )
            return {
                "count": data["meta"]["totalItems"],
                "exact": True,
                "newest": [{"id": t["id"], "name": t.get("name"), "timestamp": t.get("timestamp")} for t in data["data"]],
            }
        traces = matching_traces(integration_id, filter_ids or [])
    except SourceError as e:
        raise InputError(str(e)) from None
    return {"count": len(traces), "exact": True, "newest": traces[::-1][:sample]}
