"""Creating, changing, promoting, purging and deleting pipelines.

A pipeline has inputs [{integration_id, filter_ids}] (each connection with one or more filters; a trace matches when
any of its connection's filters selects it; a folder takes no filters), an optional output integration, `memory`
(how its trajectories are stored there: namespaces, guideline generation), and a mode: test (send to memory one
trajectory at a time, delete what it produced) or production (send every new trajectory automatically)."""

import shutil

from ..core.clock import now
from ..core.errors import InputError
from ..core.ids import unique_id
from ..core.paths import ID, PIPES, pipe_dir
from ..core.store import LOCK, write_json
from ..sources.http import SourceError
from .filters import get_filter
from .integrations import client_for, get_integration
from .kinds import KINDS, MODES
from .log import log
from .originals import delete_originals
from .runtime import RUNTIME
from .sending import send_all_progress, stop_send_all
from .state import all_pipelines, clean_memory, counts, get_pipeline, memory_of, reset_watermarks, state_of
from .trajectories import write_index


def list_pipelines():
    out = []
    for p in all_pipelines():
        p["counts"] = counts(p["id"])
        p["worker"] = RUNTIME.status(p["id"])
        p["send_all"] = send_all_progress(p["id"])
        out.append(p)
    return sorted(out, key=lambda p: p.get("created_at") or "", reverse=True)


def pipeline_view(pipeline_id):
    p = get_pipeline(pipeline_id)
    return {**p, "counts": counts(pipeline_id), "worker": RUNTIME.status(pipeline_id), "send_all": send_all_progress(pipeline_id)}


def save_pipeline(data, pipeline_id=None):
    name = (data.get("name") or "").strip()
    if not name:
        raise InputError("Missing name")
    inputs = []
    for i, x in enumerate(data.get("inputs") or [], 1):
        integ = get_integration(x.get("integration_id"))
        if not integ or KINDS[integ["kind"]]["direction"] != "input":
            raise InputError(f"Input {i}: choose an input connection")
        fids = list(dict.fromkeys(x.get("filter_ids") or [])) if KINDS[integ["kind"]].get("filter_kind") else []
        if not fids and KINDS[integ["kind"]].get("filter_kind"):
            raise InputError(f"Input {i}: choose at least one filter")
        for fid in fids:
            if get_filter(fid)["kind"] != KINDS[integ["kind"]].get("filter_kind"):
                raise InputError(f"Input {i}: filter {fid} is not a {KINDS[integ['kind']]['label']} filter")
        inputs.append({"integration_id": integ["id"], "filter_ids": fids})
    if not inputs:
        raise InputError("Add at least one input: a connection and its filters")
    output_id = data.get("output_id") or None
    if output_id:
        out = get_integration(output_id)
        if not out or KINDS[out["kind"]]["direction"] != "output":
            raise InputError("The output must be an output integration (altk-evolve)")
    mode = data.get("mode") or "test"
    if mode not in MODES:
        raise InputError(f"Mode is one of {', '.join(MODES)}")
    if mode == "production" and not output_id:
        raise InputError("A production pipeline needs an output")
    memory = clean_memory(data.get("memory"))
    try:
        poll = max(15, int(data.get("poll_seconds") or 60))
        settle = max(0, int(data.get("settle_seconds") if data.get("settle_seconds") not in (None, "") else 120))
    except ValueError:
        raise InputError("Poll and settle times are whole seconds") from None
    with LOCK:
        if pipeline_id:
            p = get_pipeline(pipeline_id)
        else:
            taken = {d.name for d in PIPES.glob("*")} if PIPES.exists() else set()
            requested = (data.get("id") or "").strip()
            if requested and (not ID.match(requested) or requested in taken):
                raise InputError(f"Id {requested!r} is taken or invalid (lowercase letters, digits, - and _)")
            p = {"id": requested or unique_id(name, taken), "created_at": now(), "runs": [], "enabled": True}
        old_inputs = p.get("inputs")
        p.update(
            name=name,
            description=(data.get("description") or "").strip() or None,
            inputs=inputs,
            output_id=output_id,
            memory=memory,
            mode=mode,
            poll_seconds=poll,
            settle_seconds=settle,
            enabled=bool(data.get("enabled", p.get("enabled", True))),
            updated_at=now(),
        )
        if mode == "production" and not p.get("promoted_at"):
            p["promoted_at"] = now()
        write_json(pipe_dir(p["id"]) / "pipeline.json", p)
    if old_inputs is not None and old_inputs != inputs:
        reset_watermarks(p["id"])
    RUNTIME.wake()
    return pipeline_view(p["id"])


def set_enabled(pipeline_id, enabled):
    with LOCK:
        p = get_pipeline(pipeline_id)
        p["enabled"] = bool(enabled)
        write_json(pipe_dir(pipeline_id) / "pipeline.json", p)
    if enabled:
        RUNTIME.resume(pipeline_id)
    else:
        RUNTIME.cancel(pipeline_id)  # a run in progress stops after the trace it is converting
    RUNTIME.wake()
    return pipeline_view(pipeline_id)


def promote(pipeline_id):
    """Test -> production: from now on every new trajectory goes to memory automatically. Trajectories the test
    produced but did not send are sent too."""
    p = get_pipeline(pipeline_id)
    if not p.get("output_id"):
        raise InputError("Choose an output (altk-evolve) first")
    with LOCK:
        p.update(mode="production", promoted_at=now(), updated_at=now())
        write_json(pipe_dir(pipeline_id) / "pipeline.json", p)
    RUNTIME.wake()
    return pipeline_view(pipeline_id)


def delete_pipeline(pipeline_id, purge_memory=False):
    p = get_pipeline(pipeline_id)
    stop_send_all(pipeline_id)
    if purge_memory:
        purge(pipeline_id, memory=True, trajectories=False)
    RUNTIME.cancel(pipeline_id)
    try:
        with RUNTIME.pipeline_lock(pipeline_id):
            shutil.rmtree(pipe_dir(p["id"]))
    finally:
        RUNTIME.resume(pipeline_id)


def purge(pipeline_id, memory=True, trajectories=True, trace_ids=None):
    """Delete what a pipeline produced: its entities in memory and/or its trajectories (which also forgets the traces
    and deletes their snapshots, so the next poll downloads and processes them again). Only for test pipelines, except
    memory removal on delete."""
    p = get_pipeline(pipeline_id)
    if p["mode"] == "production" and trajectories:
        raise InputError("A production pipeline keeps what it produced; switch it to test first")
    root = pipe_dir(pipeline_id)
    deleted = {"entities": 0, "trajectories": 0, "traces": 0, "errors": []}
    stop_send_all(pipeline_id)  # it stops after the turn it is sending
    RUNTIME.cancel(pipeline_id)  # a run in progress stops after the trace it is converting, then this goes ahead
    try:
        _purge(p, pipeline_id, root, deleted, memory, trajectories, trace_ids)
    finally:
        if p.get("enabled", True):
            RUNTIME.resume(pipeline_id)
    log(
        pipeline_id,
        f"Purged: {deleted['entities']} memory entities, {deleted['trajectories']} trajectories, {deleted['traces']} trace snapshots"
        + (f"; errors: {'; '.join(deleted['errors'])}" if deleted["errors"] else ""),
    )
    return deleted


def _purge(p, pipeline_id, root, deleted, memory, trajectories, trace_ids):
    with RUNTIME.pipeline_lock(pipeline_id):
        mem = memory_of(pipeline_id)
        targets = trace_ids if trace_ids is not None else list(mem)
        if memory and targets:
            if not p.get("output_id"):
                raise InputError("The pipeline has no output to delete from")
            ev = client_for(p["output_id"], p)
            by_ns = {}
            for tid in targets:
                for s in (mem.get(tid) or {}).get("slices", []):
                    by_ns.setdefault(s["namespace"], (set(), set()))
                    by_ns[s["namespace"]][0].update(s.get("entity_ids", []))
                    by_ns[s["namespace"]][1].add(tid)
            for ns, (ids, tids) in by_ns.items():
                try:
                    deleted["entities"] += ev.delete_for_traces(ns, ids, tids)
                except SourceError as e:
                    deleted["errors"].append(f"{ns}: {e}")
            if not deleted["errors"]:
                for tid in targets:
                    mem.pop(tid, None)
                write_json(root / "memory.json", mem)
        if trajectories:
            st = state_of(pipeline_id)
            ids = trace_ids if trace_ids is not None else list(st["traces"])
            files = [root / "trajectories" / f"{tid}.trajectory.json" for tid in ids]
            if trace_ids is None:  # everything: also files a crashed run wrote before it recorded them
                files = sorted((root / "trajectories").glob("*.trajectory.json")) if (root / "trajectories").exists() else []
            for f in files:
                if f.exists():
                    f.unlink()
                    deleted["trajectories"] += 1
            # The trace is forgotten, so the next poll downloads it again: its snapshot goes with its trajectory.
            deleted["traces"] += delete_originals(pipeline_id, trace_ids)
            for tid in ids:
                st["traces"].pop(tid, None)
                mem.pop(tid, None)
            if trace_ids is None:
                st["watermarks"] = {}
            write_json(root / "state.json", st)
            write_json(root / "memory.json", mem)
            write_index(pipeline_id)
