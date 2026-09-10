"""Evolve-owned CronJob controller and service scheduling lifecycle."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any
from contextlib import contextmanager
from collections.abc import Iterator

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.frontend.services.context import execution_cancelled
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
            result = self.client.retention(namespace, agent_id=definition.agent_id).run(
                definition.policy_id, run_id=job_id, dry_run=definition.dry_run, actor_id=job["actor_id"]
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


@contextmanager
def retention_runtime(client: EvolveClient) -> Iterator[None]:
    """Run scheduling for an Evolve service's lifetime; join on shutdown.

    Embedded hosts can use this context in their own application lifespan.
    Claims stay durable across service restarts; uncertain work is not retried.
    """
    if not client.config.retention_scheduler_enabled:
        yield
        return
    scheduler = RetentionScheduler(client)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                scheduler.run(stop=stop, poll_seconds=client.config.retention_poll_seconds, max_workers=client.config.retention_max_workers)
            except Exception:
                logger.exception("Retention scheduling interrupted; retrying after the polling interval")
                stop.wait(client.config.retention_poll_seconds)

    thread = threading.Thread(target=serve, name="evolve-retention")
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
