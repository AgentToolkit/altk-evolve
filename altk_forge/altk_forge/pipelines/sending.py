"""Sending trajectories to the pipeline's memory, slice by slice, and recording what memory created.

In test mode one turn is sent at a time (or all of them in a background thread, send_all) and can be undone; in
production the worker sends every new trajectory."""

import threading
import traceback

from ..core.clock import now
from ..core.errors import InputError
from ..core.paths import pipe_dir
from ..core.store import LOCK, write_json
from ..sources.http import SourceError
from ..trajectory.slices import slices
from .integrations import client_for
from .log import log
from .runtime import RUNTIME
from .state import get_pipeline, memory_of, namespace_settings, state_of, unsent
from .trajectories import trajectory, write_index

SENDING: dict = {}  # (pipeline_id, trace_id) -> progress of a send in progress


SEND_ALL: dict = {}  # pipeline_id -> the last send-all (test): {state, total, done, sent, failed, entities, ...}


def send_trace(pipeline_id, trace_id, client=None, revision=None, known=None):
    """Send one trajectory to the pipeline's output, slice by slice; records what memory created."""
    p = get_pipeline(pipeline_id)
    if not p.get("output_id"):
        raise InputError("The pipeline has no output; choose an altk-evolve integration")
    doc = trajectory(pipeline_id, trace_id)
    if doc is None:
        raise InputError(f"No trajectory {trace_id} in this pipeline")
    ev = client or client_for(p["output_id"], p)
    known = known if known is not None else set()
    ns = namespace_settings(pipeline_id)
    if SENDING.get((pipeline_id, trace_id)):
        raise InputError("This turn is being sent right now")
    SENDING[(pipeline_id, trace_id)] = {"state": "waiting", "since": now(), "done": 0, "total": None}
    try:
        return _send_trace(p, pipeline_id, trace_id, doc, ev, revision, known, ns)
    finally:
        SENDING.pop((pipeline_id, trace_id), None)


def send_progress(pipeline_id, trace_id):
    """What a send in progress is doing ({state, done, total, step, since}), or None."""
    return SENDING.get((pipeline_id, trace_id))


def _send_trace(p, pipeline_id, trace_id, doc, ev, revision, known, ns):
    progress = SENDING[(pipeline_id, trace_id)]
    busy = RUNTIME.status(pipeline_id) or {}
    if busy.get("state") == "running":
        progress["state"] = f"waiting for the worker ({busy.get('phase') or 'busy'})"
        log(pipeline_id, f"Send {trace_id}: waiting for the worker, which is {busy.get('phase') or 'busy'}")
    with RUNTIME.pipeline_lock(pipeline_id):
        mem = memory_of(pipeline_id)
        previous = mem.get(trace_id) or {}
        if previous.get("sent_at"):
            raise InputError("Already sent; delete it from memory first to send it again")
        # After a failed attempt, keep the slices that did go through and send only the rest.
        record = {"slices": list(previous.get("slices") or []), "started_at": now()}
        done = {s["span_id"] for s in record["slices"]}
        try:
            revision = revision if revision is not None else ev.ensure_profile()
            todo = [x for x in slices(doc, pipeline_id, ns["namespace"], ns["prefix"]) if x["span_id"] not in done]
            progress.update(state="sending", total=len(todo) + len(done), done=len(done))
            log(pipeline_id, f"Sending {trace_id}: {len(todo)} slices to altk-evolve")
            for i, x in enumerate(todo, 1):
                progress.update(step=x["step"])
                r = ev.send({"namespace_id": x["namespace_id"], "trajectory": x["trajectory"]}, revision, known)
                record["slices"].append({"span_id": x["span_id"], "step": x["step"], **r})
                progress["done"] += 1
                log(
                    pipeline_id,
                    f"  slice {i}/{len(todo)} {x['step']} -> {x['namespace_id']}: "
                    + (f"{len(r['guidelines'])} guideline{'s' * (len(r['guidelines']) != 1)}" if r["guidelines"] else "nothing generated")
                    + (f", merged into {', '.join(r['updated_ids'])}" if r.get("updated_ids") else "")
                    + "".join(f"\n    - {g}" for g in r["guidelines"]),
                )
            record["sent_at"] = now()
        except (SourceError, InputError) as e:
            record["error"] = str(e)
        mem[trace_id] = record
        write_json(pipe_dir(pipeline_id) / "memory.json", mem)
        write_index(pipeline_id)
    if record.get("error"):
        log(pipeline_id, f"SEND FAIL {trace_id}: {record['error']}")
        raise InputError(record["error"])
    n = sum(len(s["entity_ids"]) for s in record["slices"])
    productive = sum(1 for s in record["slices"] if s["entity_ids"])
    log(pipeline_id, f"Sent {trace_id}: {len(record['slices'])} slices, {productive} of them created something, {n} entities")
    return record


def send_all(pipeline_id):
    """Test: send every trajectory not in memory yet, one after another, in a background thread. Progress in
    send_all_progress(); a failed turn is recorded and the next one is sent."""
    p = get_pipeline(pipeline_id)
    if p["mode"] == "production":
        raise InputError("A production pipeline sends on its own")
    if not p.get("output_id"):
        raise InputError("The pipeline has no output; choose an altk-evolve integration")
    with LOCK:
        job = SEND_ALL.get(pipeline_id)
        if job and job["state"] in ("running", "stopping"):
            raise InputError("This pipeline is sending already")
        todo = unsent(pipeline_id)
        if not todo:
            raise InputError("Every trajectory is in memory already")
        job = SEND_ALL[pipeline_id] = {
            "state": "running",
            "total": len(todo),
            "done": 0,
            "sent": 0,
            "failed": 0,
            "slices": 0,
            "entities": 0,
            "guidelines": 0,
            "errors": [],
            "current": None,
            "started_at": now(),
            "finished_at": None,
        }
    log(pipeline_id, f"Send all: {len(todo)} trajectories to altk-evolve")
    threading.Thread(target=_send_all, args=(pipeline_id, todo, job), name=f"send-all-{pipeline_id}", daemon=True).start()
    return job


def _send_all(pipeline_id, todo, job):
    try:
        p = get_pipeline(pipeline_id)
        ev = client_for(p["output_id"], p)
        revision, known = ev.ensure_profile(), set()
        for tid in todo:
            if job["state"] == "stopping":
                break
            job["current"] = {"trace_id": tid, "name": (state_of(pipeline_id)["traces"].get(tid) or {}).get("name")}
            try:
                if get_pipeline(pipeline_id)["mode"] == "production":
                    raise InputError("promoted to production meanwhile; the worker sends the rest")
                r = send_trace(pipeline_id, tid, ev, revision, known)
                job["sent"] += 1
                job["slices"] += len(r["slices"])
                job["entities"] += sum(len(s["entity_ids"]) for s in r["slices"])
                job["guidelines"] += sum(len(s.get("guidelines") or []) for s in r["slices"])
            except InputError as e:
                if not pipe_dir(pipeline_id).exists() or "production" in str(e):
                    job["errors"].append(str(e))
                    break
                job["failed"] += 1
                job["errors"] = (job["errors"] + [f"{tid}: {e}"])[-20:]
            job["done"] += 1
    except (SourceError, InputError) as e:
        job["errors"].append(str(e))
    except Exception as e:  # report every failure
        job["errors"].append(f"{type(e).__name__}: {e}")
        log(pipeline_id, f"ERROR send all: {e}\n{traceback.format_exc()}")
    job.update(state="stopped" if job["state"] == "stopping" or job["done"] < job["total"] else "done", current=None, finished_at=now())
    if pipe_dir(pipeline_id).exists():
        log(
            pipeline_id,
            f"Send all {job['state']}: {job['sent']} of {job['total']} sent, {job['failed']} failed, "
            f"{job['entities']} entities, {job['guidelines']} guidelines",
        )


def send_all_progress(pipeline_id):
    """The last send-all of this pipeline, with the slice of the turn being sent ({..., current: {trace_id, name,
    progress}}), or None."""
    job = SEND_ALL.get(pipeline_id)
    if not job:
        return None
    out = dict(job)
    if out["current"]:
        out["current"] = {**out["current"], "progress": send_progress(pipeline_id, out["current"]["trace_id"])}
    return out


def stop_send_all(pipeline_id):
    job = SEND_ALL.get(pipeline_id)
    if job and job["state"] == "running":
        job["state"] = "stopping"
    return send_all_progress(pipeline_id)


def memory_guidelines(pipeline_id, namespace):
    p = get_pipeline(pipeline_id)
    if not p.get("output_id"):
        raise InputError("The pipeline has no output")
    try:
        return client_for(p["output_id"], p).guidelines(namespace)
    except SourceError as e:
        raise InputError(str(e)) from None


def retry_send(pipeline_id, trace_id=None):
    """Clear failed send attempts so the worker (production) tries again."""
    with RUNTIME.pipeline_lock(pipeline_id):
        mem = memory_of(pipeline_id)
        for tid, m in mem.items():
            if (trace_id is None or tid == trace_id) and not m.get("sent_at"):
                m.pop("attempts", None)
        write_json(pipe_dir(pipeline_id) / "memory.json", mem)
    RUNTIME.wake(pipeline_id)
