"""Transport-neutral schedule operations with explicit namespace scope."""

from __future__ import annotations

import datetime as dt
from typing import Any

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.retention.schedule import CronJobSpec, ScheduleDefinition
from altk_evolve.retention.schedule_store import ScheduleStore


def preview(spec: dict[str, Any], *, after: str | None = None, count: int = 5) -> dict[str, Any]:
    parsed = CronJobSpec.model_validate(spec)
    if not 1 <= count <= 20:
        raise ValueError("count must be between 1 and 20")
    instant = dt.datetime.fromisoformat(after) if after else dt.datetime.now(dt.UTC)
    instants = []
    for _ in range(count):
        instant = parsed.next_time(instant)
        instants.append(instant.isoformat())
    return {"spec": parsed.model_dump(), "next_runs": instants, "suspended": parsed.suspend}


class ScheduleService:
    def __init__(self, client: EvolveClient, namespace_id: str):
        if not namespace_id.strip():
            raise ValueError("namespace_id is required")
        self.store = ScheduleStore(client)
        self.namespace = namespace_id

    def put(self, schedule_id: str, definition: dict[str, Any], actor_id: str, expected_revision: int = 0) -> dict[str, Any]:
        if len(schedule_id) > 128:
            raise ValueError("schedule_id must be at most 128 characters")
        return self.store.put(
            self.namespace, schedule_id, ScheduleDefinition.model_validate(definition), actor_id, expected_revision=expected_revision
        )

    def get(self, schedule_id: str) -> dict[str, Any]:
        record = self.store.get(self.namespace, schedule_id)
        if record is None:
            raise ValueError("Schedule not found")
        return record

    def list(self) -> dict[str, Any]:
        return {"items": self.store.list_schedules(self.namespace)}

    def delete(self, schedule_id: str, expected_revision: int) -> dict[str, Any]:
        return {"deleted": self.store.delete(self.namespace, schedule_id, expected_revision)}

    def jobs(self, schedule_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        return {"items": self.store.jobs(self.namespace, schedule_id=schedule_id, limit=limit)}

    def cancel(self, job_id: str) -> dict[str, Any]:
        return {"cancellation_requested": self.store.cancel(self.namespace, job_id)}

    def recover(self, job_id: str, worker_stopped: bool) -> dict[str, Any]:
        if not worker_stopped:
            raise ValueError("Confirm the owning worker has stopped before acknowledging an interrupted job")
        return {"acknowledged": self.store.acknowledge_interrupted(self.namespace, job_id)}
