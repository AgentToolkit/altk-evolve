"""Retention catalog management and execution under the main Evolve CLI."""

import json
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated, Any

import typer

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.frontend.services.schedules import ScheduleService, preview
from altk_evolve.retention.policy import RetentionPolicy
from altk_evolve.schema.exceptions import EvolveException

Namespace = Annotated[str, typer.Option("--namespace", "-n", help="Service-instance namespace (required).")]
Actor = Annotated[str, typer.Option("--actor", help="Operator identity recorded in the audit history.")]
DefinitionFile = Annotated[Path, typer.Option("--file", "-f", exists=True, dir_okay=False, readable=True, help="JSON schedule definition.")]
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


def read_definition(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Schedule definition must be a JSON object")
    return value


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
    def create(schedule_id: str, namespace: Namespace, actor: Actor, file: DefinitionFile):
        """Create a schedule; fail if its ID already exists in this namespace."""
        emit(service(namespace).put(schedule_id, read_definition(file), actor, expected_revision=0))

    @schedules.command("update")
    @command_errors
    def update(schedule_id: str, namespace: Namespace, actor: Actor, file: DefinitionFile, revision: Revision):
        """Replace the full definition, including timing, suspension, and dry-run mode."""
        emit(service(namespace).put(schedule_id, read_definition(file), actor, expected_revision=revision))

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
        file: DefinitionFile,
        after: Annotated[str | None, typer.Option(help="Starting ISO timestamp including timezone.")] = None,
        count: Annotated[int, typer.Option(min=1, max=20)] = 5,
    ):
        """Preview a definition's timing without connecting to storage."""
        from altk_evolve.retention.schedule import ScheduleDefinition

        definition = ScheduleDefinition.model_validate(read_definition(file))
        emit(preview(definition.spec.model_dump(), after=after, count=count))

    @policies.command("put")
    @command_errors
    def put_policy(
        policy_id: str,
        namespace: Namespace,
        file: Annotated[Path, typer.Option("--file", "-f", exists=True, dir_okay=False, help="YAML or JSON retention policy.")],
        name: Annotated[str | None, typer.Option()] = None,
        enabled: Annotated[bool, typer.Option("--enabled/--disabled")] = True,
    ):
        """Create or replace a stored policy for scheduled execution."""
        policy = RetentionPolicy.from_file(str(file))
        catalog = service(namespace).store
        emit(
            catalog.put_policy(
                namespace_id=namespace,
                policy_id=policy_id,
                name=name or policy_id,
                description=None,
                enabled=enabled,
                policy=policy.model_dump(mode="json"),
            )
        )

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
