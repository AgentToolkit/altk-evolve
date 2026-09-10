"""Retention catalog management and execution under the main Evolve CLI."""

import json
from collections.abc import Callable
from functools import wraps
from enum import Enum
from typing import Annotated, Any

import typer

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.frontend.services.schedules import ScheduleService, preview
from altk_evolve.retention.policy import RetentionRule
from altk_evolve.schema.exceptions import EvolveException

Namespace = Annotated[str, typer.Option("--namespace", "-n", help="Service-instance namespace (required).")]
Actor = Annotated[str, typer.Option("--actor", help="Operator identity recorded in the audit history.")]
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

    def service(namespace: str) -> ScheduleService:
        return ScheduleService(get_client(), namespace)

    @schedules.command("create")
    @command_errors
    def create(
        schedule_id: str,
        namespace: Namespace,
        actor: Actor,
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
        emit(service(namespace).put(schedule_id, definition, actor, expected_revision=0))

    @schedules.command("update")
    @command_errors
    def update(
        schedule_id: str,
        namespace: Namespace,
        actor: Actor,
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
        catalog = service(namespace)
        record = catalog.get(schedule_id)
        if record["revision"] != revision:
            raise ValueError("Schedule revision conflict")
        definition = record["definition"]
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
        emit(catalog.put(schedule_id, definition, actor, expected_revision=revision))

    @schedules.command("get")
    @command_errors
    def get(schedule_id: str, namespace: Namespace):
        """Read a schedule, its definition, and current revision."""
        emit(service(namespace).get(schedule_id))

    @schedules.command("list")
    @command_errors
    def list_schedules(namespace: Namespace):
        """List schedules in one service instance."""
        emit(service(namespace).list())

    @schedules.command("delete")
    @command_errors
    def delete(schedule_id: str, namespace: Namespace, revision: Revision):
        """Delete an inactive schedule at the specified revision."""
        result = service(namespace).delete(schedule_id, revision)
        if not result["deleted"]:
            raise ValueError("Schedule not found")
        emit(result)

    @schedules.command("preview")
    @command_errors
    def preview_schedule(
        schedule: Annotated[str, typer.Option(help="Five-field cron expression.")],
        time_zone: Annotated[str, typer.Option()] = "Etc/UTC",
        after: Annotated[str | None, typer.Option(help="Starting ISO timestamp including timezone.")] = None,
        count: Annotated[int, typer.Option(min=1, max=20)] = 5,
    ):
        """Preview timing without connecting to storage."""
        emit(preview({"schedule": schedule, "timeZone": time_zone}, after=after, count=count))

    def existing_policy(namespace, policy_id):
        catalog = service(namespace).store
        record = catalog.get_policy(namespace_id=namespace, policy_id=policy_id)
        if record is None:
            raise ValueError("Policy not found")
        return catalog, record

    def save_policy(catalog, record):
        return catalog.put_policy(**{key: record[key] for key in ("namespace_id", "policy_id", "name", "description", "enabled", "policy")})

    @policies.command("put")
    @command_errors
    def put_policy(
        policy_id: str,
        namespace: Namespace,
        name: Annotated[str | None, typer.Option()] = None,
        enabled: Annotated[bool | None, typer.Option("--enabled/--disabled")] = None,
    ):
        """Create a policy or update its name/status, preserving existing rules."""
        from altk_evolve.retention.schedule import ScheduleDefinition

        # Reuse the catalog's policy identifier constraints.
        ScheduleDefinition.model_validate({"policy_id": policy_id, "spec": {"schedule": "@daily"}})
        catalog = service(namespace).store
        record = catalog.get_policy(namespace_id=namespace, policy_id=policy_id) or {
            "namespace_id": namespace,
            "policy_id": policy_id,
            "name": policy_id,
            "description": None,
            "enabled": True,
            "policy": {"rules": []},
        }
        if name is not None:
            record["name"] = name
        if enabled is not None:
            record["enabled"] = enabled
        emit(save_policy(catalog, record))

    @policies.command("set-rule")
    @command_errors
    def set_rule(
        policy_id: str,
        rule_name: str,
        namespace: Namespace,
        max_age_days: Annotated[int | None, typer.Option(min=0)] = None,
        max_unused_days: Annotated[int | None, typer.Option(min=0)] = None,
        entity_type: Annotated[str | None, typer.Option()] = None,
        action: Annotated[Action, typer.Option()] = Action.flag,
        on_missing_access_signal: Annotated[MissingAccess, typer.Option()] = MissingAccess.skip,
        cascade_derived: Annotated[bool, typer.Option()] = False,
    ):
        """Append a rule or replace a named rule in place. First matching rule wins."""
        rule = RetentionRule.model_validate(
            {
                "name": rule_name,
                "max_age_days": max_age_days,
                "max_unused_days": max_unused_days,
                "entity_type": entity_type,
                "action": action.value,
                "on_missing_access_signal": on_missing_access_signal.value,
                "cascade_derived": cascade_derived,
            }
        )
        catalog, record = existing_policy(namespace, policy_id)
        rules = record["policy"]["rules"]
        matches = [index for index, item in enumerate(rules) if item["name"] == rule_name]
        if len(matches) > 1:
            raise ValueError("Policy contains duplicate rule names; cannot replace unambiguously")
        if matches:
            rules[matches[0]] = rule.model_dump(mode="json")
        else:
            rules.append(rule.model_dump(mode="json"))
        emit(save_policy(catalog, record))

    @policies.command("remove-rule")
    @command_errors
    def remove_rule(policy_id: str, rule_name: str, namespace: Namespace):
        """Remove rules with this name from the stored policy."""
        catalog, record = existing_policy(namespace, policy_id)
        rules = record["policy"]["rules"]
        remaining = [rule for rule in rules if rule["name"] != rule_name]
        if len(remaining) == len(rules):
            raise ValueError("Rule not found")
        record["policy"]["rules"] = remaining
        emit(save_policy(catalog, record))

    @policies.command("get")
    @command_errors
    def get_policy(policy_id: str, namespace: Namespace):
        result = service(namespace).store.get_policy(namespace_id=namespace, policy_id=policy_id)
        if result is None:
            raise ValueError("Policy not found")
        emit(result)

    @policies.command("list")
    @command_errors
    def list_policies(namespace: Namespace):
        emit({"items": service(namespace).store.list_policies(namespace_id=namespace, include_disabled=True)})

    @jobs.command("list")
    @command_errors
    def list_jobs(
        namespace: Namespace,
        schedule: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    ):
        emit(service(namespace).jobs(schedule_id=schedule, limit=limit))

    @jobs.command("get")
    @command_errors
    def get_job(job_id: str, namespace: Namespace):
        catalog = service(namespace).store
        result = catalog.get_job(namespace, job_id)
        if result is None:
            raise ValueError("Job not found")
        emit({"job": result, "run": catalog.get_run(namespace_id=namespace, run_id=job_id)})

    @jobs.command("cancel")
    @command_errors
    def cancel_job(job_id: str, namespace: Namespace):
        emit(service(namespace).cancel(job_id))

    @jobs.command("recover")
    @command_errors
    def recover_job(
        job_id: str,
        namespace: Namespace,
        worker_stopped: Annotated[bool, typer.Option("--worker-stopped", help="Confirm the owning worker has stopped.")] = False,
    ):
        """Mark interrupted work after confirming worker shutdown; never retry it."""
        emit(service(namespace).recover(job_id, worker_stopped))

    @app.command("run")
    @command_errors
    def run_policy(
        policy_id: str,
        namespace: Namespace,
        actor: Actor,
        agent: Annotated[str | None, typer.Option()] = None,
        apply: Annotated[bool, typer.Option("--apply", help="Apply mutations; defaults to dry run.")] = False,
    ):
        """Run a stored policy immediately and persist its audit report."""
        from altk_evolve.frontend.mcp.mcp_server import run_retention
        from altk_evolve.frontend.services.context import use_client

        client = get_client()
        ScheduleService(client, namespace)  # Validate explicit namespace before resolving the operation.
        if not actor.strip():
            raise ValueError("Actor identity must be nonblank")
        with use_client(client):
            result = json.loads(
                run_retention(
                    policy_id,
                    namespace_id=namespace,
                    actor_id=actor,
                    dry_run=not apply,
                    metadata_filters=json.dumps({"agent_id": agent}) if agent else None,
                )
            )
        emit(result)
        if result.get("error") or result.get("errors"):
            raise typer.Exit(1)

    @app.command("execute")
    @command_errors
    def execute(
        once: Annotated[bool, typer.Option("--once", help="Dispatch due schedules, drain queued jobs, and exit.")] = False,
        poll_seconds: Annotated[float, typer.Option(min=0.001, help="Seconds between scheduling checks.")] = 10,
        max_workers: Annotated[int, typer.Option(min=1, help="Maximum simultaneous executions in this process.")] = 1,
    ):
        """Execute stored schedules across namespaces; continuous unless --once is set."""
        from altk_evolve.retention.scheduler import run_worker

        run_worker(get_client(), once=once, poll_seconds=poll_seconds, max_workers=max_workers)
