"""The trajectories a pipeline produced: .data/pipelines/<id>/trajectories/<trace_id>.trajectory.json and index.json."""

import json
import re

from ..core.errors import InputError
from ..core.paths import pipe_dir
from ..core.store import read_json, write_json
from ..trajectory.slices import slices
from ..trajectory.summary import summary
from .state import memory_of, namespace_settings

# The ids trajectories may have (trajectory.schema.json): also what keeps a trace id from naming another folder.
TRACE_ID = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


def write_index(pipeline_id):
    """Rebuild trajectories/index.json (the list the viewer shows) from every trajectory file of the pipeline."""
    root = pipe_dir(pipeline_id) / "trajectories"
    mem = memory_of(pipeline_id)
    index = []
    for f in sorted(root.glob("*.trajectory.json")) if root.exists() else []:
        try:
            doc = json.loads(f.read_text())
        except ValueError:
            continue
        index.append(_row(doc, f.name, mem))
    return _save_index(root, index)


def update_index(pipeline_id, doc):
    """Put one trajectory's row into index.json, replacing its old row, without reading the others: the viewer shows
    each trajectory as soon as it is built, while a long run goes on converting."""
    root = pipe_dir(pipeline_id) / "trajectories"
    index = [r for r in read_json(root / "index.json", []) if r.get("trace_id") != doc["trace_id"]]
    index.append(_row(doc, f"{doc['trace_id']}.trajectory.json", memory_of(pipeline_id)))
    return _save_index(root, index)


def _row(doc, file_name, mem):
    row = summary(doc, file_name)
    m = mem.get(doc["trace_id"]) or {}
    row["memory"] = (
        {
            "sent_at": m.get("sent_at"),
            "error": m.get("error"),
            "entities": sum(len(s.get("entity_ids", [])) for s in m.get("slices", [])),
        }
        if m
        else None
    )
    return row


def _save_index(root, index):
    index.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    write_json(root / "index.json", index)
    return index


def trajectory(pipeline_id, trace_id):
    if not TRACE_ID.match(trace_id or ""):
        raise InputError("Invalid id")
    f = pipe_dir(pipeline_id) / "trajectories" / f"{trace_id}.trajectory.json"
    return json.loads(f.read_text()) if f.exists() else None


def slices_of(pipeline_id, trace_id):
    doc = trajectory(pipeline_id, trace_id)
    if doc is None:
        return None
    ns = namespace_settings(pipeline_id)
    out = slices(doc, pipeline_id, ns["namespace"], ns["prefix"])
    sent = {s["span_id"]: s for s in (memory_of(pipeline_id).get(trace_id) or {}).get("slices", [])}
    for x in out:
        x["sent"] = sent.get(x["span_id"])
    return {"slices": out, "memory": memory_of(pipeline_id).get(trace_id)}
