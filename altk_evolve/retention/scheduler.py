"""Evolve-owned CronJob controller and executable retention worker."""

from __future__ import annotations

import datetime as dt
import json
import logging
import signal
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.frontend.services.context import use_client, execution_cancelled
from altk_evolve.retention.schedule import ScheduleDefinition
from altk_evolve.retention.schedule_store import ScheduleStore

logger = logging.getLogger(__name__)


class RetentionScheduler:
    """Dispatch and claim jobs durably; each execution reuses the manual run service."""

    def __init__(self, client: EvolveClient):
        self.client = client
        self.store = ScheduleStore(client)
        self.worker_id = str(uuid.uuid4())

    def dispatch(self, now: dt.datetime | None = None) -> list[str]:
        now = now or dt.datetime.now(dt.UTC)
        admitted = []
        for schedule in self.store.list_schedules():
            try:
                job = self.store.dispatch(schedule["namespace_id"], schedule["schedule_id"], now)
                if job:
                    admitted.append(job)
            except Exception:
                logger.exception("Unable to dispatch retention schedule %s", schedule["schedule_id"])
        return admitted

    def execute(self, job: dict[str, Any]) -> None:
        """Claim once; preserve failed, cancelled, or partial execution in run history."""
        from altk_evolve.frontend.mcp.mcp_server import run_retention

        namespace, job_id = job["namespace_id"], job["job_id"]
        claimed = self.store.claim(namespace, job_id, self.worker_id, dt.datetime.now(dt.UTC))
        if claimed is None:
            return
        job = claimed
        definition = ScheduleDefinition.model_validate(job["definition"])
        token = execution_cancelled.set(lambda: self.store.cancelled(namespace, job_id, self.worker_id))
        try:
            if self.store.cancelled(namespace, job_id, self.worker_id):
                self.store.finish(namespace, job_id, self.worker_id, "cancelled")
                return
            with use_client(self.client):
                result = json.loads(
                    run_retention(
                        policy_id=definition.policy_id,
                        namespace_id=namespace,
                        run_id=job_id,
                        dry_run=definition.dry_run,
                        actor_id=job["actor_id"],
                        metadata_filters=json.dumps({"agent_id": definition.agent_id}) if definition.agent_id else None,
                    )
                )
            status = "cancelled" if result.get("cancelled") else ("failed" if result.get("error") or result.get("errors") else "completed")
            self.store.finish(namespace, job_id, self.worker_id, status, result.get("error"))
        except Exception as exc:
            logger.exception("Retention job %s failed", job_id)
            self.store.finish(namespace, job_id, self.worker_id, "failed", type(exc).__name__)
        finally:
            execution_cancelled.reset(token)

    def run(self, *, once: bool = False, poll_seconds: float = 10, max_workers: int = 1, stop: threading.Event | None = None) -> None:
        """Run until stopped; once mode drains currently admitted work and exits."""
        if poll_seconds <= 0 or max_workers < 1:
            raise ValueError("poll_seconds and max_workers must be positive")
        stop = stop or threading.Event()
        pending: dict[str, tuple[str, Future[None]]] = {}
        dispatched = False
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            while not stop.is_set():
                pending = {key: value for key, value in pending.items() if not value[1].done()}
                if not once or not dispatched:
                    self.dispatch()
                    dispatched = True
                for job in self.store.queued(max_workers):
                    if len(pending) >= max_workers:
                        break
                    if job["job_id"] not in pending:
                        pending[job["job_id"]] = (job["namespace_id"], pool.submit(self.execute, job))
                if once:
                    for _, future in pending.values():
                        future.result()
                    if not self.store.queued(1):
                        return
                    continue
                stop.wait(poll_seconds)
            for job_id, (namespace, _) in pending.items():
                self.store.cancel(namespace, job_id)


def run_worker(client: EvolveClient, *, once: bool = False, poll_seconds: float = 10, max_workers: int = 1) -> None:
    """Execute schedules with graceful signal handling for `evolve retention execute`."""
    stop = threading.Event()
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
        RetentionScheduler(client).run(once=once, poll_seconds=poll_seconds, max_workers=max_workers, stop=stop)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
