"""CLI profile management and trajectory processing against the configured database."""

import json
from pathlib import Path
from typing import Annotated

import typer

from altk_evolve.processing import ProfileReference

profiles_app = typer.Typer(help="Manage processing profiles in the configured database.")
processors_app = typer.Typer(help="Discover installed trajectory processors.")
processing_app = typer.Typer(help="Run trajectory processors.")


def client():
    from altk_evolve.cli.cli import get_client

    return get_client()


@processors_app.command("list")
def list_processors():
    typer.echo(json.dumps(client().processing.registry.inventory(), indent=2))


@profiles_app.command("get")
def get_profile(name: str, revision: int | None = None):
    try:
        typer.echo(json.dumps(client().processing.get(name, revision), indent=2))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@profiles_app.command("apply")
def apply_profile(
    name: str,
    file: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    expected_revision: Annotated[int, typer.Option(min=0, help="0 creates; otherwise the last observed revision")],
):
    try:
        result = client().processing.put(name, json.loads(file.read_text()), expected_revision=expected_revision)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, indent=2))


@processing_app.command("run")
def run(
    file: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    namespace: Annotated[str, typer.Option()],
    processing_profile: Annotated[str, typer.Option()],
    revision: Annotated[int | None, typer.Option(min=1)] = None,
):
    try:
        result = client().process_trajectory(
            json.loads(file.read_text()),
            namespace_id=namespace,
            processing_profile=ProfileReference(id=processing_profile, revision=revision),
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(result.model_dump_json(indent=2))
