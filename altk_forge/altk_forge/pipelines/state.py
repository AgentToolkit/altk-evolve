"""What a pipeline is and what it has done, as stored on disk:

.data/pipelines/<id>/pipeline.json   definition, run history
.data/pipelines/<id>/state.json      every trace it has processed: trace_id -> {updated_at, status, ...},
                                     and the watermark per input; a processed trace is never converted again
.data/pipelines/<id>/memory.json     what was sent to the output: trace_id -> {slices: [{span_id, namespace,
                                     entity_ids, guidelines}], sent_at}"""

import json
import re

from ..converters.store import converter_ids, list_converters
from ..core.errors import InputError
from ..core.paths import PIPES, pipe_dir
from ..core.store import LOCK, read_json, write_json
from .kinds import MEMORY_CHOICES, MEMORY_FIELDS
from .runtime import RUNTIME


def all_pipelines():
    return [json.loads(p.read_text()) for p in sorted(PIPES.glob("*/pipeline.json"))] if PIPES.exists() else []


def get_pipeline(pipeline_id):
    p = read_json(pipe_dir(pipeline_id) / "pipeline.json", None)
    if p is None:
        raise InputError(f"No pipeline {pipeline_id}")
    return p


def pipeline_integrations(p):
    return {i["integration_id"] for i in p.get("inputs", [])} | ({p["output_id"]} if p.get("output_id") else set())


def state_of(pipeline_id):
    return read_json(pipe_dir(pipeline_id) / "state.json", {"traces": {}, "watermarks": {}})


def memory_of(pipeline_id):
    return read_json(pipe_dir(pipeline_id) / "memory.json", {})


def converters_state(pipeline_id):
    """Which converters, at which version, the pipeline has: a failed trace is tried again when this changes."""
    return ",".join(f"{c['id']}v{c['version']}" for c in list_converters(pipeline_id))


def counts(pipeline_id):
    st, mem = state_of(pipeline_id), memory_of(pipeline_id)
    by = {}
    for t in st["traces"].values():
        by[t["status"]] = by.get(t["status"], 0) + 1
    return {
        "processed": len(st["traces"]),
        "trajectories": by.get("built", 0),
        "failed": by.get("failed", 0),
        "sent": sum(1 for m in mem.values() if m.get("sent_at")),
        "unsent": len(unsent(pipeline_id, st, mem)),
        "send_failed": sum(1 for m in mem.values() if m.get("error") and not m.get("sent_at")),
        "entities": sum(len(s.get("entity_ids", [])) for m in mem.values() for s in m.get("slices", [])),
        "converters": len(converter_ids(pipeline_id)),
    }


def unsent(pipeline_id, st=None, mem=None):
    """Trace ids with a trajectory not in memory yet (failed sends too), oldest first."""
    st = st if st is not None else state_of(pipeline_id)
    mem = mem if mem is not None else memory_of(pipeline_id)
    return [
        tid
        for tid, t in sorted(st["traces"].items(), key=lambda kv: kv[1].get("timestamp") or "")
        if t["status"] == "built" and not (mem.get(tid) or {}).get("sent_at")
    ]


def namespace_settings(pipeline_id):
    mem = get_pipeline(pipeline_id).get("memory") or {}
    if mem.get("namespace_mode") == "single":
        return {"namespace": mem["namespace_id"], "prefix": None}
    return {"namespace": None, "prefix": mem.get("namespace_prefix") or pipeline_id}


def clean_memory(data):
    """A pipeline's memory settings as stored: empty values dropped, choices and numbers checked."""
    out = {}
    for f in MEMORY_FIELDS:
        v = (data or {}).get(f)
        v = v.strip() if isinstance(v, str) else "" if v is None else str(v)
        if not v:
            continue
        if f in MEMORY_CHOICES and v not in MEMORY_CHOICES[f]:
            raise InputError(f"{f} must be one of {', '.join(c for c in MEMORY_CHOICES[f] if c)}")
        if f in ("profile_revision", "trajectory_max_steps", "trajectory_max_step_chars", "trajectory_tail_steps") and not v.isdigit():
            raise InputError(f"{f} must be a whole number")
        out[f] = v
    if out.get("namespace_mode") == "single" and not out.get("namespace_id"):
        raise InputError("One namespace for everything needs its name")
    for f in ("namespace_id", "namespace_prefix"):
        if out.get(f) and not re.match(r"^[a-z_][a-z0-9_]*$", out[f]):
            raise InputError(f"{f}: lowercase letters, digits and _ only, not starting with a digit")
    if out.get("profile_revision") and not out.get("processing_profile"):
        raise InputError("A pinned profile revision needs the profile's name")
    return out


def reset_watermarks(pipeline_id):
    """List every matching trace again on the next poll (traces already processed are still skipped)."""
    with LOCK:
        st = state_of(pipeline_id)
        st["watermarks"] = {}
        write_json(pipe_dir(pipeline_id) / "state.json", st)
    RUNTIME.wake()
