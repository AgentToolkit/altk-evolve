"""Public retention operations shared by all application interfaces."""

from __future__ import annotations

import datetime as dt
import re
from functools import wraps
from typing import Any, TYPE_CHECKING, ParamSpec, TypeVar
from collections.abc import Callable

from pydantic import ValidationError

from altk_evolve.retention.execution import execute_policy
from altk_evolve.retention.policy import RetentionPolicy, RetentionRule
from altk_evolve.retention.schedule import ScheduleDefinition, CronJobSpec
from altk_evolve.retention.schedule_store import ScheduleStore

if TYPE_CHECKING:
    from altk_evolve.frontend.client.evolve_client import EvolveClient


class RetentionError(ValueError):
    """Transport-neutral error with an HTTP-compatible status and safe details."""

    def __init__(self, message: str, status: int = 400, details: Any = None):
        super().__init__(message)
        self.status = status
        self.details = details

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {"error": str(self)}
        if isinstance(self.details, dict) and "run_id" in self.details:
            result.update(self.details)
        elif self.details is not None:
            result["details"] = self.details
        return result


P = ParamSpec("P")
R = TypeVar("R")


def operation(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except RetentionError:
            raise
        except ValueError as exc:
            message = str(exc)
            status = (
                404
                if "not found" in message.lower()
                else (409 if any(word in message.lower() for word in ("conflict", "already exists", "referenced", "active jobs")) else 400)
            )
            details = exc.errors(include_context=False, include_input=False) if isinstance(exc, ValidationError) else None
            raise RetentionError(message, status, details) from exc

    return wrapped


class RetentionService:
    """Namespace-bound API. The caller authenticates and authorizes this scope.

    Optional agent scope constrains schedules, jobs, and executions. Policies are
    reusable namespace-wide definitions. No method crosses into another namespace.
    """

    def __init__(self, client: EvolveClient, namespace_id: str, *, agent_id: str | None = None, store: ScheduleStore | None = None):
        if not namespace_id.strip() or (agent_id is not None and not agent_id.strip()):
            raise RetentionError("Namespace and supplied agent identity must be nonblank")
        self.client = client
        self.namespace_id = namespace_id
        self.agent_id = agent_id
        self.store = store if store is not None else ScheduleStore(client)

    @staticmethod
    def _policy_id(policy_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:\-]{1,128}", policy_id):
            raise RetentionError("Invalid retention policy id")

    @staticmethod
    def _actor(actor_id: str | None) -> None:
        if actor_id is not None and not actor_id.strip():
            raise RetentionError("Actor identity must be nonblank")

    @staticmethod
    @operation
    def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
        return RetentionPolicy.model_validate(policy).model_dump(mode="json")

    @operation
    def create_policy(self, policy_id: str, *, name: str | None = None, enabled: bool = True) -> dict[str, Any]:
        self._policy_id(policy_id)
        if name is not None and not name.strip():
            raise RetentionError("name is required")
        self.client.ensure_namespace(self.namespace_id)
        return self.store.create_policy(self.namespace_id, policy_id, name or policy_id, enabled)

    @operation
    def update_policy(self, policy_id: str, *, name: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
        if name is not None and not name.strip():
            raise RetentionError("name is required")
        return self.store.update_policy(self.namespace_id, policy_id, name, enabled)

    @operation
    def put_policy(
        self, policy_id: str, *, name: str, policy: dict[str, Any], description: str | None = None, enabled: bool = True
    ) -> dict[str, Any]:
        """Replace a complete policy document; partial edits use update_policy/rules."""
        self._policy_id(policy_id)
        if not name.strip():
            raise RetentionError("name is required")
        normalized = self.validate_policy(policy)
        self.client.ensure_namespace(self.namespace_id)
        return self.store.put_policy(
            namespace_id=self.namespace_id,
            policy_id=policy_id,
            name=name.strip(),
            description=description.strip() if description and description.strip() else None,
            enabled=enabled,
            policy=normalized,
        )

    @operation
    def get_policy(self, policy_id: str) -> dict[str, Any]:
        self._policy_id(policy_id)
        record = self.store.get_policy(namespace_id=self.namespace_id, policy_id=policy_id)
        if record is None:
            raise RetentionError(f"Retention policy {policy_id!r} not found", 404)
        return record

    @operation
    def list_policies(self, *, include_disabled: bool = False) -> dict[str, Any]:
        return {"items": self.store.list_policies(namespace_id=self.namespace_id, include_disabled=include_disabled)}

    @operation
    def delete_policy(self, policy_id: str) -> dict[str, bool]:
        self.store.delete_policy(self.namespace_id, policy_id)
        return {"deleted": True}

    @operation
    def add_rule(self, policy_id: str, name: str, rule: dict[str, Any]) -> dict[str, Any]:
        # Reject unknown options rather than silently accepting misspelled rule fields.
        self._rule_fields(rule)
        return self.store.edit_rule(self.namespace_id, policy_id, name, "add", rule)

    @staticmethod
    def _rule_fields(values: dict[str, Any]) -> None:
        if set(values) - (set(RetentionRule.model_fields) - {"name"}):
            raise RetentionError("Unknown or immutable rule field")

    @operation
    def update_rule(self, policy_id: str, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        self._rule_fields(changes)
        return self.store.edit_rule(self.namespace_id, policy_id, name, "update", changes)

    @operation
    def list_rules(self, policy_id: str) -> dict[str, Any]:
        return {"items": self.get_policy(policy_id)["policy"]["rules"]}

    @operation
    def remove_rule(self, policy_id: str, name: str) -> dict[str, Any]:
        return self.store.edit_rule(self.namespace_id, policy_id, name, "remove", {})

    def _schedule_scope(self, record: dict[str, Any]) -> None:
        if self.agent_id is not None and record["definition"]["agent_id"] != self.agent_id:
            raise RetentionError("Schedule not found", 404)

    @operation
    def create_schedule(self, schedule_id: str, definition: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        return self.put_schedule(schedule_id, definition, actor_id=actor_id, expected_revision=0)

    @operation
    def put_schedule(self, schedule_id: str, definition: dict[str, Any], *, actor_id: str, expected_revision: int = 0) -> dict[str, Any]:
        self._actor(actor_id)
        if len(schedule_id) > 128 or expected_revision < 0:
            raise RetentionError("Invalid schedule ID or revision")
        parsed = ScheduleDefinition.model_validate(definition)
        if self.agent_id is not None and parsed.agent_id != self.agent_id:
            raise RetentionError("Schedule agent must match the authorized scope", 403)
        if expected_revision:
            self.get_schedule(schedule_id)
        return self.store.put(self.namespace_id, schedule_id, parsed, actor_id, expected_revision=expected_revision)

    @operation
    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        record = self.store.get(self.namespace_id, schedule_id)
        if record is None:
            raise RetentionError("Schedule not found", 404)
        self._schedule_scope(record)
        spec = CronJobSpec.model_validate(record["definition"]["spec"])
        record["next_runs"] = []
        if not spec.suspend:
            instant = dt.datetime.now(dt.UTC)
            for _ in range(5):
                instant = spec.next_time(instant)
                record["next_runs"].append(instant.isoformat())
        return record

    @operation
    def list_schedules(self) -> dict[str, Any]:
        return {
            "items": [
                record
                for record in self.store.list_schedules(self.namespace_id)
                if self.agent_id is None or record["definition"]["agent_id"] == self.agent_id
            ]
        }

    @operation
    def update_schedule(self, schedule_id: str, changes: dict[str, Any], *, actor_id: str, expected_revision: int) -> dict[str, Any]:
        record = self.get_schedule(schedule_id)
        if record["revision"] != expected_revision:
            raise RetentionError("Schedule revision conflict", 409)
        if set(changes) - {"policy_id", "agent_id", "dry_run", "spec"}:
            raise RetentionError("Unknown schedule field")
        definition = record["definition"]
        definition.update({key: value for key, value in changes.items() if key != "spec"})
        if "spec" in changes:
            if not isinstance(changes["spec"], dict):
                raise RetentionError("spec must be an object")
            definition["spec"].update(changes["spec"])
        return self.put_schedule(schedule_id, definition, actor_id=actor_id, expected_revision=expected_revision)

    @operation
    def start_schedule(self, schedule_id: str, *, actor_id: str, expected_revision: int) -> dict[str, Any]:
        return self.update_schedule(schedule_id, {"spec": {"suspend": False}}, actor_id=actor_id, expected_revision=expected_revision)

    @operation
    def stop_schedule(self, schedule_id: str, *, actor_id: str, expected_revision: int) -> dict[str, Any]:
        return self.update_schedule(schedule_id, {"spec": {"suspend": True}}, actor_id=actor_id, expected_revision=expected_revision)

    @operation
    def delete_schedule(self, schedule_id: str, *, expected_revision: int) -> dict[str, bool]:
        self.get_schedule(schedule_id)
        return {"deleted": self.store.delete(self.namespace_id, schedule_id, expected_revision)}

    @operation
    def list_jobs(self, *, schedule_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 1000:
            raise RetentionError("limit must be between 1 and 1000")
        return {
            "items": [
                record
                for record in self.store.jobs(self.namespace_id, schedule_id=schedule_id, limit=limit)
                if self.agent_id is None or record["definition"]["agent_id"] == self.agent_id
            ]
        }

    @operation
    def get_job(self, job_id: str) -> dict[str, Any]:
        record = self.store.get_job(self.namespace_id, job_id)
        if record is None or (self.agent_id is not None and record["definition"]["agent_id"] != self.agent_id):
            raise RetentionError("Job not found", 404)
        record["run"] = self.store.get_run(namespace_id=self.namespace_id, run_id=job_id)
        return record

    @operation
    def cancel_job(self, job_id: str) -> dict[str, bool]:
        self.get_job(job_id)
        return {"cancellation_requested": self.store.cancel(self.namespace_id, job_id)}

    @operation
    def recover_job(self, job_id: str, *, worker_stopped: bool) -> dict[str, bool]:
        self.get_job(job_id)
        if not worker_stopped:
            raise RetentionError("Confirm the owning worker has stopped before acknowledging an interrupted job")
        return {"acknowledged": self.store.acknowledge_interrupted(self.namespace_id, job_id)}

    @operation
    def run(
        self,
        policy_id: str,
        *,
        actor_id: str | None = None,
        dry_run: bool = True,
        as_of: str | dt.datetime | None = None,
        scan_limit: int | None = None,
        run_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        additional_matches: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._actor(actor_id)
        self._policy_id(policy_id)
        if scan_limit is not None and scan_limit <= 0:
            raise RetentionError("scan_limit must be greater than zero")
        now = dt.datetime.fromisoformat(as_of.replace("Z", "+00:00")) if isinstance(as_of, str) else as_of
        if now is not None and now.tzinfo is None:
            now = now.replace(tzinfo=dt.UTC)
        if additional_matches is not None and (
            not isinstance(additional_matches, list) or any(not isinstance(v, dict) for v in additional_matches)
        ):
            raise RetentionError("additional_matches must be a JSON array of objects")
        filters = dict(metadata_filters or {})
        if self.agent_id is not None:
            if "agent_id" in filters and filters["agent_id"] != self.agent_id:
                raise RetentionError("Agent filter must match the authorized scope", 403)
            filters["agent_id"] = self.agent_id
        result = execute_policy(
            self.client,
            self.store,
            self.namespace_id,
            policy_id,
            dry_run=dry_run,
            actor_id=actor_id,
            as_of=now,
            scan_limit=scan_limit,
            run_id=run_id,
            metadata_filters=filters or None,
            additional_matches=additional_matches,
        )
        if "error" in result:
            message = result["error"]
            raise RetentionError(
                message,
                404 if "not found" in message else (500 if "run_id" in result else 400),
                {key: value for key, value in result.items() if key != "error"},
            )
        return result

    @operation
    def list_runs(self, *, policy_id: str | None = None, limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise RetentionError("limit must be between 1 and 200")
        if policy_id is not None:
            self._policy_id(policy_id)
        return {"items": self.store.list_runs(namespace_id=self.namespace_id, agent_id=self.agent_id, policy_id=policy_id, limit=limit)}

    @operation
    def get_run(self, run_id: str) -> dict[str, Any]:
        record = self.store.get_run(namespace_id=self.namespace_id, run_id=run_id)
        if record is None or (self.agent_id is not None and record["agent_id"] != self.agent_id):
            raise RetentionError("Run not found", 404)
        return record
