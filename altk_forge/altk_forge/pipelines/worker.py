"""The worker that runs pipelines in the background.

It polls every enabled pipeline (poll_seconds), asks each input for traces newer than its watermark (minus an
overlap, because ingestion is asynchronous), and converts those it has not processed. A trace is taken once it has
not changed for settle_seconds (Langfuse's updatedAt), so scores written after the run are in the trajectory.
A folder has no watermark: modification times can be older than files already seen (a copy that keeps them), so
every poll lists the whole folder and skips the files it has processed; a file is taken once it has not been
modified for settle_seconds."""

import json
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from ..converters.engine import convert_trace
from ..converters.errors import ConverterError
from ..core.clock import iso, now, parse
from ..core.errors import InputError
from ..core.paths import pipe_dir
from ..core.store import LOCK, write_json
from ..llm import LLMError
from ..sources.http import SourceError
from .filters import matching_traces
from .integrations import client_for, get_integration
from .kinds import KINDS
from .log import log
from .originals import save_original
from .runtime import RUNTIME
from .sending import send_trace
from .state import all_pipelines, converters_state, get_pipeline, memory_of, state_of
from .trajectories import write_index

OVERLAP = timedelta(minutes=10)  # ingestion is asynchronous: re-list a window before the watermark


RETRIES = 3  # conversions of one trace, counting the first


class Cancelled(Exception):
    """A run stopped because the pipeline was paused, purged or deleted while it ran."""


class Worker:
    """Runs every enabled pipeline in the background: poll the inputs, convert new traces, and in production send
    the new trajectories to memory. One thread; a pipeline can also be run once on demand (run_now)."""

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pipeline-worker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        RUNTIME.wake()

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def run_now(self, pipeline_id):
        get_pipeline(pipeline_id)
        RUNTIME.resume(pipeline_id)
        if not self.running:  # no background worker (e.g. CLI): run in this thread
            return self.run_once(pipeline_id)
        RUNTIME.wake(pipeline_id)
        return {"queued": True}

    def _loop(self):
        while not self._stop.is_set():
            for p in all_pipelines():
                if self._stop.is_set():
                    break
                if not p.get("enabled", True):
                    RUNTIME.set_paused(p["id"])
                    continue
                if time.time() >= RUNTIME.due(p["id"]):
                    self.run_once(p["id"])
                    at = time.time() + p.get("poll_seconds", 60)
                    RUNTIME.schedule(p["id"], at)
                    RUNTIME.status_of(p["id"])["next_poll"] = iso(datetime.fromtimestamp(at, timezone.utc))
            RUNTIME.wait(max(1.0, RUNTIME.next_due(time.time() + 30) - time.time()))

    def run_once(self, pipeline_id):
        """One poll of one pipeline. Returns the run record."""
        lock = RUNTIME.pipeline_lock(pipeline_id)
        if not lock.acquire(blocking=False):
            return {"busy": True}
        run = {
            "id": uuid.uuid4().hex[:8],
            "started_at": now(),
            "found": 0,
            "new": 0,
            "built": 0,
            "failed": 0,
            "waiting": 0,
            "sent": 0,
            "send_failed": 0,
        }
        st_ = RUNTIME.status_of(pipeline_id)
        st_.update(state="running", phase="listing", message=None)
        try:
            p = get_pipeline(pipeline_id)
            self._poll(p, run, st_)
            if p["mode"] == "production" and p.get("output_id"):
                st_["phase"] = "sending"
                self._send_pending(p, run)
            run["status"] = "ok"
        except Cancelled as e:
            run.update(status="stopped", message=str(e))
            log(pipeline_id, f"STOPPED {e}")
        except InputError as e:
            run.update(status="failed", message=str(e))
            log(pipeline_id, f"ERROR {e}")
        except Exception as e:  # report every failure, never kill the worker
            run.update(status="failed", message=f"{type(e).__name__}: {e}")
            log(pipeline_id, f"ERROR {run['message']}\n{traceback.format_exc()}")
        finally:
            lock.release()
        run["finished_at"] = now()
        st_.update(state="idle", phase=None, phase_since=None, last_poll=run["finished_at"], message=run.get("message"), last_run=run)
        if (
            run["new"] or run["sent"] or run["failed"] or run["send_failed"] or run.get("converters_changed") or run["status"] != "ok"
        ) and not (run["status"] == "stopped" and not pipe_dir(pipeline_id).exists()):
            self._record(pipeline_id, run)
        return run

    def _record(self, pipeline_id, run):
        with LOCK:
            try:
                p = get_pipeline(pipeline_id)
            except InputError:
                return  # deleted meanwhile
            p["runs"] = ([run] + p.get("runs", []))[:50]
            write_json(pipe_dir(pipeline_id) / "pipeline.json", p)

    def _poll(self, p, run, status):
        root = pipe_dir(p["id"])
        st = state_of(p["id"])
        settle = timedelta(seconds=p.get("settle_seconds", 120))
        changed = False
        for inp in p["inputs"]:
            key = inp["integration_id"]
            integ = get_integration(key)
            if not integ:
                raise InputError(f"Input connection {key} no longer exists")
            mark = st["watermarks"].get(key)
            since = iso(parse(mark) - OVERLAP) if mark and KINDS[integ["kind"]].get("watermark", True) else None
            try:
                traces = matching_traces(key, inp["filter_ids"], since)
            except SourceError as e:
                raise InputError(f"{integ['name']}: {e}") from None
            run["found"] += len(traces)
            client = client_for(key)
            newest_done = mark
            blocked = False  # the watermark stops before the first trace still settling
            # Traces that failed are tried again once the pipeline's converters have changed since (a converter written
            # or extended for another trace may handle them), up to RETRIES times; retry_failed() asks for it now.
            listed = {t["id"] for t in traces}
            for tid, prev in st["traces"].items():
                if (
                    tid not in listed
                    and prev.get("status") == "failed"
                    and prev.get("integration_id") == key
                    and prev.get("attempts", 1) < RETRIES
                    and prev.get("converters_seen") != converters_state(p["id"])
                ):
                    traces.append(
                        {
                            "id": tid,
                            "name": prev.get("name"),
                            "timestamp": prev.get("timestamp"),
                            "updated_at": prev.get("updated_at"),
                            "filters": prev.get("filters", []),
                            "retry": True,
                        }
                    )
            for t in traces:
                if RUNTIME.cancelled(p["id"]):
                    if changed:
                        write_json(root / "state.json", st)
                        write_index(p["id"])
                    raise Cancelled(
                        f"stopped after {run['new']} new trace{'s' * (run['new'] != 1)}: the pipeline was paused, purged or deleted"
                    )
                done = st["traces"].get(t["id"])
                if (
                    done
                    and t.get("retry") is None
                    and done.get("status") == "failed"
                    and done.get("attempts", 1) < RETRIES
                    and done.get("converters_seen") != converters_state(p["id"])
                ):
                    t["retry"] = True
                if done and not t.get("retry"):
                    if not blocked and (not newest_done or t["timestamp"] > newest_done):
                        newest_done = t["timestamp"]
                    continue
                age = datetime.now(timezone.utc) - (parse(t["updated_at"]) or datetime.now(timezone.utc))
                if age < settle and not t.get("retry"):
                    run["waiting"] += 1
                    blocked = True
                    continue
                status["phase"] = "converting"
                run["new"] += 1
                info, raw = None, None
                try:
                    raw = client.trace(t["id"])
                    save_original(p["id"], t["id"], raw, client)
                    doc, problems, info = convert(p, raw, integ, status)
                except SourceError as e:
                    raise InputError(f"{integ['name']}: {e}") from None
                except LLMError as e:  # the model is down or not configured: stop here, the trace is tried again
                    run["new"] -= 1
                    if changed:
                        write_json(root / "state.json", st)
                        write_index(p["id"])
                    raise InputError(f"Converter LLM: {e}") from None
                except Cancelled:
                    run["new"] -= 1  # not processed: the next run converts it
                    if changed:
                        write_json(root / "state.json", st)
                        write_index(p["id"])
                    raise
                except Exception as e:
                    doc, problems = None, [f"{type(e).__name__}: {e}"]
                entry = {
                    "updated_at": (raw.get("updatedAt") if isinstance(raw, dict) else None) or t["updated_at"],
                    "timestamp": t["timestamp"],
                    "name": t.get("name"),
                    "integration_id": key,
                    "filters": t["filters"],
                    "processed_at": now(),
                    **({"converter": info["converter"], "converter_version": info["version"]} if info else {}),
                }
                if info and info.get("changed"):
                    run["converters_changed"] = run.get("converters_changed", 0) + 1
                if t.get("retry"):
                    entry["attempts"] = (done or {}).get("attempts", 1) + 1
                    log(p["id"], f"Retrying {t['id']} (attempt {entry['attempts']} of {RETRIES}): the converters changed since it failed")
                if problems:
                    entry.update(status="failed", problems=problems[:10], converters_seen=converters_state(p["id"]))
                    run["failed"] += 1
                    log(p["id"], f"FAIL {t['id']} ({t.get('name')}): " + "".join(f"\n    - {x}" for x in problems[:10]))
                else:
                    (root / "trajectories").mkdir(parents=True, exist_ok=True)
                    (root / "trajectories" / f"{t['id']}.trajectory.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
                    entry["status"] = "built"
                    run["built"] += 1
                    log(p["id"], f"Built {t['id']} ({t.get('name')})")
                st["traces"][t["id"]] = entry
                changed = True
                if not blocked and (not newest_done or t["timestamp"] > newest_done):
                    newest_done = t["timestamp"]
                if run["new"] % 10 == 0:  # keep progress across a crash
                    write_json(root / "state.json", st)
            if newest_done != mark:
                st["watermarks"][key] = newest_done
                changed = True
        if changed:
            write_json(root / "state.json", st)
        if run["built"] or not (root / "trajectories" / "index.json").exists():
            write_index(p["id"])

    def _send_pending(self, p, run):
        st, mem = state_of(p["id"]), memory_of(p["id"])
        pending = [
            tid
            for tid, t in sorted(st["traces"].items(), key=lambda kv: kv[1].get("timestamp") or "")
            if t["status"] == "built" and not (mem.get(tid) or {}).get("sent_at") and (mem.get(tid) or {}).get("attempts", 0) < 3
        ]
        if not pending:
            return
        ev = client_for(p["output_id"], p)
        revision, known = ev.ensure_profile(), set()
        for tid in pending:
            try:
                send_trace(p["id"], tid, ev, revision, known)
                run["sent"] += 1
            except InputError:
                run["send_failed"] += 1
                with RUNTIME.pipeline_lock(p["id"]):
                    mem = memory_of(p["id"])
                    mem.setdefault(tid, {})["attempts"] = mem.get(tid, {}).get("attempts", 0) + 1
                    write_json(pipe_dir(p["id"]) / "memory.json", mem)


WORKER = Worker()


def convert(p, raw, integ, status=None):
    """(trajectory, problems, converter info or None) for one raw trace, with the code the LLM wrote for this shape
    of trace (converters.engine)."""
    if status is not None:
        status.update(phase=f"converting {raw.get('name') or raw.get('id')}", phase_since=now())
    context = {"host": integ.get("host"), "source_kind": integ["kind"], "pipeline_id": p["id"]}

    def note(line, phase=None):
        if line is None and RUNTIME.cancelled(p["id"]):  # between LLM attempts: nothing is saved yet
            raise Cancelled("stopped while writing a converter: the pipeline was paused, purged or deleted")
        if line:
            log(p["id"], line)
        if status is not None and phase:
            status.update(phase=phase, phase_since=now())

    with RUNTIME.pipeline_lock(p["id"]):
        try:
            doc, problems, info = convert_trace(p["id"], raw, context, note)
        except ConverterError as e:
            return None, [str(e)], None
    return doc, problems, info


def retry_failed(pipeline_id, trace_id=None):
    """Convert failed traces again on the next poll, whatever the converters."""
    with RUNTIME.pipeline_lock(pipeline_id):
        st = state_of(pipeline_id)
        n = 0
        for tid, e in st["traces"].items():
            if (trace_id is None or tid == trace_id) and e.get("status") == "failed":
                e.pop("converters_seen", None)
                e["attempts"] = 0
                n += 1
        write_json(pipe_dir(pipeline_id) / "state.json", st)
    RUNTIME.wake(pipeline_id)
    return {"queued": n}
