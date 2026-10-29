"""What the worker, the senders and the API share while Forge runs, per pipeline: a lock (one conversion or send at a
time), the worker's status, whether a run in progress is to stop, and when the worker polls it next.

`RUNTIME` is the one instance. The worker (pipelines.worker) polls through it; everything else only reads its
status, takes its locks, cancels runs and wakes the worker up."""

import threading

from ..core.store import LOCK


class Runtime:
    def __init__(self):
        self._wake = threading.Event()
        self._locks = {}
        self._status = {}  # pipeline_id -> {state, phase, last_poll, next_poll, message, ...}
        self._due = {}  # pipeline_id -> when the worker polls it next (time.time())
        self._cancel = set()  # pipelines whose run in progress is to stop

    # ---- runs in progress
    def cancel(self, pipeline_id):
        self._cancel.add(pipeline_id)

    def resume(self, pipeline_id):
        self._cancel.discard(pipeline_id)

    def cancelled(self, pipeline_id):
        return pipeline_id in self._cancel

    def pipeline_lock(self, pipeline_id):
        with LOCK:
            return self._locks.setdefault(pipeline_id, threading.RLock())

    # ---- the worker's status of a pipeline
    def status(self, pipeline_id):
        """A copy of the status, or None before the worker has looked at the pipeline."""
        return dict(self._status.get(pipeline_id) or {}) or None

    def status_of(self, pipeline_id):
        """The status itself, for the worker to update in place while it runs."""
        return self._status.setdefault(pipeline_id, {})

    def set_paused(self, pipeline_id):
        self._status[pipeline_id] = {**(self._status.get(pipeline_id) or {}), "state": "paused", "next_poll": None}

    # ---- when the worker polls
    def wake(self, pipeline_id=None):
        """Poll `pipeline_id` now (or just re-check every pipeline's schedule)."""
        if pipeline_id:
            self._due[pipeline_id] = 0
        self._wake.set()

    def due(self, pipeline_id):
        return self._due.get(pipeline_id, 0)

    def schedule(self, pipeline_id, at):
        self._due[pipeline_id] = at

    def next_due(self, default):
        return min(list(self._due.values()) + [default])

    def wait(self, seconds):
        """Sleep until `seconds` have passed or someone calls wake()."""
        self._wake.wait(seconds)
        self._wake.clear()


RUNTIME = Runtime()
