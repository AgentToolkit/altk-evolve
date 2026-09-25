"""Retention catalog management and execution under the main Evolve CLI."""

import json
from collections.abc import Callable
from functools import wraps
from enum import Enum
from typing import Annotated, Any

import typer

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.retention.service import RetentionService
from altk_evolve.schema.exceptions import EvolveException

Namespace = Annotated[str, typer.Option("--namespace", "-n", help="Service-instance namespace (required).")]
InitiatedBy = Annotated[str, typer.Option("--initiated-by", help="Operator identity recorded in the audit history.")]
Revision = Annotated[int, typer.Option("--revision", min=1, help="Last observed revision; stale writes are rejected.")]


def command_errors(function):
    """Report expected input/storage errors on stderr with a failing exit status."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, OSError, EvolveException) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc

    return wrapped


def emit(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, default=str))


class Concurrency(str, Enum):
    allow = "Allow"
    forbid = "Forbid"
    REPLACE = "Replace"


class Action(str, Enum):
    flag = "flag"
    delete = "delete"


class MissingAccess(str, Enum):
    skip = "skip"
    flag = "flag"
    delete = "delete"


def register_retention_commands(app: typer.Typer, get_client: Callable[[], EvolveClient]) -> None:
    schedules = typer.Typer(help="Create, inspect, update, and delete retention schedules.", no_args_is_help=True)
    policies = typer.Typer(help="Store and inspect policies used by schedules.", no_args_is_help=True)
    jobs = typer.Typer(help="Inspect and cancel scheduled executions.", no_args_is_help=True)
    app.add_typer(schedules, name="schedules")
    app.add_typer(policies, name="policies")
    app.add_typer(jobs, name="jobs")
    rules = typer.Typer(help="Manage ordered rules inside a policy.", no_args_is_help=True)
    policies.add_typer(rules, name="rules")

    def service(namespace: str) -> RetentionService:
        return get_client().retention(namespace)

    @schedules.command("create")
    @command_errors
    def create(
        schedule_id: str,
        namespace: Namespace,
        initiated_by: InitiatedBy,
        policy: Annotated[str, typer.Option(help="Stored policy ID.")],
        schedule: Annotated[str, typer.Option(help="Five-field cron expression.")],
        time_zone: Annotated[str, typer.Option()] = "Etc/UTC",
        concurrency_policy: Annotated[Concurrency, typer.Option()] = Concurrency.allow,
        starting_deadline_seconds: Annotated[int | None, typer.Option(min=0)] = None,
        agent: Annotated[str | None, typer.Option()] = None,
        apply: Annotated[bool, typer.Option("--apply", help="Apply mutations; defaults to dry run.")] = False,
        suspend: Annotated[bool, typer.Option()] = False,
    ):
        """Create a persisted schedule from command options."""
        definition = {
            "policy_id": policy,
            "agent_id": agent,
            "dry_run": not apply,
            "spec": {
                "schedule": schedule,
                "timeZone": time_zone,
                "concurrencyPolicy": concurrency_policy.value,
                "startingDeadlineSeconds": starting_deadline_seconds,
                "suspend": suspend,
            },
        }
        emit(service(namespace).create_schedule(schedule_id, definition, initiated_by=initiated_by))

    @schedules.command("update")
    @command_errors
    def update(
        schedule_id: str,
        namespace: Namespace,
        initiated_by: InitiatedBy,
        revision: Revision,
        policy: Annotated[str | None, typer.Option()] = None,
        schedule: Annotated[str | None, typer.Option()] = None,
        time_zone: Annotated[str | None, typer.Option()] = None,
        concurrency_policy: Annotated[Concurrency | None, typer.Option()] = None,
        starting_deadline_seconds: Annotated[int | None, typer.Option(min=0)] = None,
        clear_deadline: Annotated[bool, typer.Option(help="Remove the starting deadline.")] = False,
        agent: Annotated[str | None, typer.Option()] = None,
        clear_agent: Annotated[bool, typer.Option(help="Target the entire namespace.")] = False,
        apply: Annotated[bool | None, typer.Option("--apply/--dry-run")] = None,
        suspend: Annotated[bool | None, typer.Option("--suspend/--resume")] = None,
    ):
        """Change only supplied fields; reject a stale revision."""
        if clear_deadline and starting_deadline_seconds is not None:
            raise ValueError("Use either --clear-deadline or --starting-deadline-seconds")
        if clear_agent and agent is not None:
            raise ValueError("Use either --clear-agent or --agent")
        definition: dict[str, Any] = {"spec": {}}
        for key, value in {"policy_id": policy, "agent_id": agent, "dry_run": None if apply is None else not apply}.items():
            if value is not None:
                definition[key] = value
        for spec_key, spec_value in {
            "schedule": schedule,
            "timeZone": time_zone,
            "concurrencyPolicy": concurrency_policy.value if concurrency_policy else None,
            "startingDeadlineSeconds": starting_deadline_seconds,
            "suspend": suspend,
        }.items():
            if spec_value is not None:
                definition["spec"][spec_key] = spec_value
        if clear_deadline:
            definition["spec"]["startingDeadlineSeconds"] = None
        if clear_agent:
            definition["agent_id"] = None
        emit(service(namespace).update_schedule(schedule_id, definition, initiated_by=initiated_by, expected_revision=revision))

    @schedules.command("show")
    @command_errors
    def show(schedule_id: str, namespace: Namespace):
        """Show configuration, revision, and the next five scheduled times in UTC."""
        emit(service(namespace).get_schedule(schedule_id))

    @schedules.command("list")
    @command_errors
    def list_schedules(namespace: Namespace):
        """List schedules in one service instance."""
        emit(service(namespace).list_schedules())

    @schedules.command("delete")
    @command_errors
    def delete(schedule_id: str, namespace: Namespace, revision: Revision):
        """Delete an inactive schedule at the specified revision."""
        result = service(namespace).delete_schedule(schedule_id, expected_revision=revision)
        if not result["deleted"]:
            raise ValueError("Schedule not found")
        emit(result)

    @schedules.command("start")
    @command_errors
    def start_schedule(schedule_id: str, namespace: Namespace, initiated_by: InitiatedBy, revision: Revision):
        """Enable future scheduled execution by the running Evolve service."""
        emit(service(namespace).start_schedule(schedule_id, initiated_by=initiated_by, expected_revision=revision))

    @schedules.command("stop")
    @command_errors
    def stop_schedule(schedule_id: str, namespace: Namespace, initiated_by: InitiatedBy, revision: Revision):
        """Suspend future runs; queued or active jobs are not cancelled."""
        emit(service(namespace).stop_schedule(schedule_id, initiated_by=initiated_by, expected_revision=revision))

    @policies.command("create")
    @command_errors
    def create_policy(
        policy_id: str,
        namespace: Namespace,
        name: Annotated[str | None, typer.Option()] = None,
        enabled: Annotated[bool, typer.Option("--enabled/--disabled")] = True,
    ):
        """Create an empty policy; fail if the ID already exists."""
        emit(service(namespace).create_policy(policy_id, name=name, enabled=enabled))

    @policies.command("update")
    @command_errors
    def update_policy(
        policy_id: str,
        namespace: Namespace,
        name: Annotated[str | None, typer.Option()] = None,
        enabled: Annotated[bool | None, typer.Option("--enabled/--disabled")] = None,
    ):
        """Change policy name/status without replacing its rules."""
        emit(service(namespace).update_policy(policy_id, name=name, enabled=enabled))

    @policies.command("delete")
    @command_errors
    def delete_policy(policy_id: str, namespace: Namespace):
        """Delete a policy only when no schedules or active jobs reference it."""
        service(namespace).delete_policy(policy_id)
        emit({"deleted": True})

    @rules.command("add")
    @command_errors
    def add_rule(
        policy_id: str,
        namespace: Namespace,
        name: Annotated[str, typer.Option(help="Unique rule name within the policy.")],
        max_age_days: Annotated[int | None, typer.Option(min=0)] = None,
        max_unused_days: Annotated[int | None, typer.Option(min=0)] = None,
        min_source_deleted_days: Annotated[int | None, typer.Option(min=0, help="Elapsed days after confirmed source deletion.")] = None,
        entity_type: Annotated[str | None, typer.Option()] = None,
        action: Annotated[Action, typer.Option()] = Action.flag,
        on_missing_access_signal: Annotated[MissingAccess, typer.Option()] = MissingAccess.skip,
        cascade_derived: Annotated[bool, typer.Option()] = False,
        source_deleted: Annotated[bool, typer.Option()] = False,
    ):
        """Append a named rule. First matching rule wins."""
        emit(
            service(namespace).add_rule(
                policy_id,
                name,
                {
                    "max_age_days": max_age_days,
                    "max_unused_days": max_unused_days,
                    "min_source_deleted_days": min_source_deleted_days,
                    "entity_type": entity_type,
                    "action": action.value,
                    "on_missing_access_signal": on_missing_access_signal.value,
                    "cascade_derived": cascade_derived,
                    "source_deleted": source_deleted,
                },
            )
        )

    @rules.command("update")
    @command_errors
    def update_rule(
        policy_id: str,
        namespace: Namespace,
        name: Annotated[str, typer.Option(help="Existing rule name.")],
        max_age_days: Annotated[int | None, typer.Option(min=0)] = None,
        max_unused_days: Annotated[int | None, typer.Option(min=0)] = None,
        min_source_deleted_days: Annotated[int | None, typer.Option(min=0, help="Elapsed days after confirmed source deletion.")] = None,
        entity_type: Annotated[str | None, typer.Option()] = None,
        action: Annotated[Action | None, typer.Option()] = None,
        on_missing_access_signal: Annotated[MissingAccess | None, typer.Option()] = None,
        cascade_derived: Annotated[bool | None, typer.Option("--cascade-derived/--no-cascade-derived")] = None,
        source_deleted: Annotated[bool | None, typer.Option("--source-deleted/--no-source-deleted")] = None,
        clear_age: Annotated[bool, typer.Option()] = False,
        clear_unused: Annotated[bool, typer.Option()] = False,
        all_types: Annotated[bool, typer.Option()] = False,
    ):
        """Change supplied fields of a named rule, keeping its position."""
        values: dict[str, Any] = {
            key: value
            for key, value in {
                "max_age_days": max_age_days,
                "max_unused_days": max_unused_days,
                "min_source_deleted_days": min_source_deleted_days,
                "entity_type": entity_type,
                "action": action.value if action else None,
                "on_missing_access_signal": on_missing_access_signal.value if on_missing_access_signal else None,
                "cascade_derived": cascade_derived,
                "source_deleted": source_deleted,
            }.items()
            if value is not None
        }
        for clear, key in [(clear_age, "max_age_days"), (clear_unused, "max_unused_days"), (all_types, "entity_type")]:
            if clear:
                if key in values:
                    raise ValueError(f"Cannot set and clear {key} together")
                values[key] = None
        emit(service(namespace).update_rule(policy_id, name, values))

    @rules.command("list")
    @command_errors
    def list_rules(policy_id: str, namespace: Namespace):
        emit(service(namespace).list_rules(policy_id))

    @rules.command("remove")
    @command_errors
    def remove_rule(policy_id: str, namespace: Namespace, name: Annotated[str, typer.Option()]):
        """Remove a named rule from the policy."""
        emit(service(namespace).remove_rule(policy_id, name))

    @policies.command("show")
    @command_errors
    def get_policy(policy_id: str, namespace: Namespace):
        result = service(namespace).get_policy(policy_id)
        if result is None:
            raise ValueError("Policy not found")
        emit(result)

    @policies.command("list")
    @command_errors
    def list_policies(namespace: Namespace):
        emit(service(namespace).list_policies(include_disabled=True))

    @jobs.command("list")
    @command_errors
    def list_jobs(
        namespace: Namespace,
        schedule: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    ):
        emit(service(namespace).list_jobs(schedule_id=schedule, limit=limit))

    @jobs.command("show")
    @command_errors
    def get_job(job_id: str, namespace: Namespace):
        result = service(namespace).get_job(job_id)
        report = result.pop("run", None)
        emit({"job": result, "run": report})

    @jobs.command("cancel")
    @command_errors
    def cancel_job(job_id: str, namespace: Namespace):
        emit(service(namespace).cancel_job(job_id))

    @jobs.command("recover")
    @command_errors
    def recover_job(
        job_id: str,
        namespace: Namespace,
        worker_stopped: Annotated[bool, typer.Option("--worker-stopped", help="Confirm the owning worker has stopped.")] = False,
    ):
        """Mark interrupted work after confirming worker shutdown; never retry it."""
        emit(service(namespace).recover_job(job_id, worker_stopped=worker_stopped))

    @app.command("run")
    @command_errors
    def run_policy(
        policy_id: str,
        namespace: Namespace,
        initiated_by: InitiatedBy,
        agent: Annotated[str | None, typer.Option()] = None,
        apply: Annotated[bool, typer.Option("--apply", help="Apply mutations; defaults to dry run.")] = False,
    ):
        """Run a stored policy immediately and persist its audit report."""
        result = get_client().retention(namespace, agent_id=agent).run(policy_id, initiated_by=initiated_by, dry_run=not apply)
        emit(result)
        if result.get("errors"):
            raise typer.Exit(1)

    @app.command("mark")
    @command_errors
    def mark_policy(policy_id: str, namespace: Namespace, initiated_by: InitiatedBy):
        """Persist deletion candidates without deleting memories."""
        emit(service(namespace).mark(policy_id, initiated_by=initiated_by))

    @app.command("sweep")
    @command_errors
    def sweep_policy(policy_id: str, namespace: Namespace, initiated_by: InitiatedBy):
        """Delete eligible, unprotected candidates and commit audit receipts."""
        emit(service(namespace).sweep(policy_id, initiated_by=initiated_by))

    @app.command("candidates")
    @command_errors
    def list_candidates(namespace: Namespace):
        emit(service(namespace).list_candidates())

    @app.command("audit")
    @command_errors
    def list_audit(namespace: Namespace):
        emit(service(namespace).list_audit())
