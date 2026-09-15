"""Container entrypoint for retention SIGKILL integration tests."""

import os
from pathlib import Path
import signal
import threading
import time
from types import SimpleNamespace


def executor():
    import psycopg

    from altk_evolve.retention.collection import Collection
    from altk_evolve.retention.scheduler import RetentionScheduler
    from altk_evolve.retention.service import RetentionService

    if kill_file := os.environ.get("TEST_KILL_FILE"):

        def kill_on_request():
            while not Path(kill_file).exists():
                time.sleep(0.1)
            os.kill(os.getpid(), signal.SIGKILL)

        threading.Thread(target=kill_on_request, daemon=True).start()

    conn = psycopg.connect(os.environ["TEST_DSN"], autocommit=True)
    client = SimpleNamespace(
        config=SimpleNamespace(backend="postgres"),
        backend=SimpleNamespace(conn=conn, _table_name=lambda ns: "ns_" + ns),
    )
    client.retention = lambda ns, agent_id=None: RetentionService(client, ns, agent_id=agent_id)
    phase = os.environ.get("TEST_PAUSE", "")
    original_event = Collection.event
    original_sweep = Collection.sweep_one
    seen = 0

    def pause():
        print("CRASH_BOUNDARY_READY", flush=True)
        # The host kills the whole container; no exception or Python cleanup runs.
        while True:
            time.sleep(1)

    def event(self, connection, candidate, outcome, run_id, initiated_by):
        nonlocal seen
        if (phase == "mark" and outcome == "marked") or (phase == "delete" and outcome == "deleted"):
            seen += 1
            if seen == 2:
                pause()
        return original_event(self, connection, candidate, outcome, run_id, initiated_by)

    def sweep(self, *args, **kwargs):
        result = original_sweep(self, *args, **kwargs)
        if phase == "committed" and result and result["outcome"] == "deleted":
            pause()
        return result

    Collection.event = event
    Collection.sweep_one = sweep
    RetentionScheduler(client).run(poll_seconds=0.5)


if __name__ == "__main__":
    executor()
