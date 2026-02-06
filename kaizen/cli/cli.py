"""Kaizen CLI for managing entities and namespaces."""

import json
import sys
import zipfile
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from kaizen.frontend.client.kaizen_client import KaizenClient
from kaizen.schema.core import Entity
from kaizen.schema.exceptions import (
    KaizenException,
    NamespaceAlreadyExistsException,
    NamespaceNotFoundException,
)

app = typer.Typer(help="Kaizen CLI - Manage entities and namespaces")
namespaces_app = typer.Typer(help="Namespace management commands")
entities_app = typer.Typer(help="Entity management commands")
sync_app = typer.Typer(help="Sync commands")
skills_app = typer.Typer(help="Skill management commands")

app.add_typer(namespaces_app, name="namespaces")
app.add_typer(entities_app, name="entities")
app.add_typer(sync_app, name="sync")
app.add_typer(skills_app, name="skills")

console = Console()


def get_client() -> KaizenClient:
    """Get a KaizenClient instance."""
    return KaizenClient()


# =============================================================================
# Namespace Commands
# =============================================================================


@namespaces_app.command("list")
def list_namespaces(
    limit: Annotated[int, typer.Option(help="Maximum number of namespaces to list")] = 10,
):
    """List all namespaces."""
    client = get_client()
    namespaces = client.all_namespaces(limit=limit)

    if not namespaces:
        console.print("[yellow]No namespaces found.[/yellow]")
        return

    table = Table(title="Namespaces")
    table.add_column("ID", style="cyan")
    table.add_column("Entities", justify="right")
    table.add_column("Created At", style="dim")

    for ns in namespaces:
        table.add_row(
            ns.id,
            str(ns.num_entities) if ns.num_entities is not None else "-",
            ns.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        )

    console.print(table)


@namespaces_app.command("create")
def create_namespace(
    namespace_id: Annotated[str, typer.Argument(help="ID for the new namespace")],
):
    """Create a new namespace."""
    client = get_client()
    try:
        ns = client.create_namespace(namespace_id)
        console.print(f"[green]Created namespace:[/green] {ns.id}")
    except NamespaceAlreadyExistsException:
        console.print(f"[red]Namespace '{namespace_id}' already exists.[/red]")
        raise typer.Exit(1)


@namespaces_app.command("delete")
def delete_namespace(
    namespace_id: Annotated[str, typer.Argument(help="ID of the namespace to delete")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
):
    """Delete a namespace and all its entities."""
    client = get_client()

    try:
        ns = client.get_namespace_details(namespace_id)
    except NamespaceNotFoundException:
        console.print(f"[red]Namespace '{namespace_id}' not found.[/red]")
        raise typer.Exit(1)

    if not force:
        entity_count = ns.num_entities or 0
        confirm = typer.confirm(f"Delete namespace '{namespace_id}' with {entity_count} entities?")
        if not confirm:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)

    client.delete_namespace(namespace_id)
    console.print(f"[green]Deleted namespace:[/green] {namespace_id}")


@namespaces_app.command("info")
def namespace_info(
    namespace_id: Annotated[str, typer.Argument(help="ID of the namespace")],
):
    """Show details about a namespace."""
    client = get_client()
    try:
        ns = client.get_namespace_details(namespace_id)
        console.print(f"[bold]Namespace:[/bold] {ns.id}")
        console.print(f"[bold]Entities:[/bold] {ns.num_entities or 'unknown'}")
        console.print(f"[bold]Created:[/bold] {ns.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
    except NamespaceNotFoundException:
        console.print(f"[red]Namespace '{namespace_id}' not found.[/red]")
        raise typer.Exit(1)


# =============================================================================
# Entity Commands
# =============================================================================


@entities_app.command("list")
def list_entities(
    namespace: Annotated[str, typer.Argument(help="Namespace to list entities from")],
    type_filter: Annotated[Optional[str], typer.Option("--type", "-t", help="Filter by entity type")] = None,
    limit: Annotated[int, typer.Option(help="Maximum number of entities to list")] = 100,
):
    """List all entities in a namespace."""
    client = get_client()

    try:
        filters = {"type": type_filter} if type_filter else None
        entities = client.get_all_entities(namespace, filters=filters, limit=limit)
    except NamespaceNotFoundException:
        console.print(f"[red]Namespace '{namespace}' not found.[/red]")
        raise typer.Exit(1)

    if not entities:
        console.print("[yellow]No entities found.[/yellow]")
        return

    table = Table(title=f"Entities in '{namespace}'")
    table.add_column("ID", style="cyan", max_width=20)
    table.add_column("Type", style="magenta")
    table.add_column("Content", max_width=60)
    table.add_column("Created At", style="dim")

    for entity in entities:
        content = entity.content
        content_str = str(content)

        if len(content_str) > 60:
            content_str = content_str[:57] + "..."
        table.add_row(
            str(entity.id),
            entity.type,
            content_str,
            entity.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(entities)} entities[/dim]")


@entities_app.command("add")
def add_entity(
    namespace: Annotated[str, typer.Argument(help="Namespace to add entity to")],
    content: Annotated[str, typer.Option("--content", "-c", help="Entity content")] = "",
    entity_type: Annotated[str, typer.Option("--type", "-t", help="Entity type")] = "guideline",
    metadata: Annotated[Optional[str], typer.Option("--metadata", "-m", help="JSON metadata")] = None,
    no_conflict_resolution: Annotated[bool, typer.Option("--no-conflict-resolution", help="Disable conflict resolution")] = False,
):
    """Add a new entity to a namespace."""
    client = get_client()

    # If no content provided, prompt for it
    if not content:
        content = typer.prompt("Entity content")

    # Parse metadata if provided
    parsed_metadata = None
    if metadata:
        try:
            parsed_metadata = json.loads(metadata)
        except json.JSONDecodeError:
            console.print("[red]Invalid JSON metadata.[/red]")
            raise typer.Exit(1)
    else:
        parsed_metadata = {}

    # Ensure namespace exists
    if not client.namespace_exists(namespace):
        create = typer.confirm(f"Namespace '{namespace}' doesn't exist. Create it?")
        if create:
            client.create_namespace(namespace)
            console.print(f"[green]Created namespace:[/green] {namespace}")
        else:
            raise typer.Exit(1)

    entity = Entity(
        content=content,
        type=entity_type,
        metadata=parsed_metadata,
    )

    try:
        results = client.update_entities(
            namespace,
            [entity],
            enable_conflict_resolution=not no_conflict_resolution,
        )
        if results:
            result = results[0]
            console.print(f"[green]Entity {result.event}:[/green] ID={result.id}")
        else:
            console.print("[yellow]No entity was added (possibly filtered by conflict resolution).[/yellow]")
    except KaizenException as e:
        console.print(f"[red]Error adding entity: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        error_msg = str(e).lower()
        if "api_key" in error_msg or "authentication" in error_msg:
            console.print("[red]Error: Conflict resolution requires an LLM API key.[/red]")
            console.print("[yellow]Either:[/yellow]")
            console.print("  1. Set OPENAI_API_KEY environment variable")
            console.print("  2. Use --no-conflict-resolution flag to skip LLM-based deduplication")
            raise typer.Exit(1)
        raise


@entities_app.command("delete")
def delete_entity(
    namespace: Annotated[str, typer.Argument(help="Namespace containing the entity")],
    entity_id: Annotated[str, typer.Argument(help="ID of the entity to delete")],
):
    """Delete an entity by ID."""
    client = get_client()

    try:
        client.delete_entity_by_id(namespace, entity_id)
        console.print(f"[green]Deleted entity:[/green] {entity_id}")
    except NamespaceNotFoundException:
        console.print(f"[red]Namespace '{namespace}' not found.[/red]")
        raise typer.Exit(1)
    except KaizenException as e:
        console.print(f"[red]Error deleting entity: {e}[/red]")
        raise typer.Exit(1)


@entities_app.command("search")
def search_entities(
    namespace: Annotated[str, typer.Argument(help="Namespace to search in")],
    query: Annotated[str, typer.Argument(help="Search query (semantic search)")],
    type_filter: Annotated[Optional[str], typer.Option("--type", "-t", help="Filter by entity type")] = None,
    limit: Annotated[int, typer.Option(help="Maximum number of results")] = 10,
):
    """Search for entities using semantic similarity."""
    client = get_client()

    try:
        filters = {"type": type_filter} if type_filter else None
        entities = client.search_entities(namespace, query=query, filters=filters, limit=limit)
    except NamespaceNotFoundException:
        console.print(f"[red]Namespace '{namespace}' not found.[/red]")
        raise typer.Exit(1)

    if not entities:
        console.print("[yellow]No matching entities found.[/yellow]")
        return

    table = Table(title=f"Search results for '{query}'")
    table.add_column("ID", style="cyan", max_width=20)
    table.add_column("Type", style="magenta")
    table.add_column("Content", max_width=60)
    table.add_column("Created At", style="dim")

    for entity in entities:
        content = entity.content
        content_str = str(content)

        if len(content_str) > 60:
            content_str = content_str[:57] + "..."
        table.add_row(
            str(entity.id),
            entity.type,
            content_str,
            entity.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)
    console.print(f"\n[dim]Found: {len(entities)} entities[/dim]")


@entities_app.command("show")
def show_entity(
    namespace: Annotated[str, typer.Argument(help="Namespace containing the entity")],
    entity_id: Annotated[str, typer.Argument(help="ID of the entity to show")],
):
    """Show full details of an entity."""
    client = get_client()

    try:
        # Search with a broad query and filter by ID
        entities = client.get_all_entities(namespace, limit=1000)
        entity = next((e for e in entities if str(e.id) == entity_id), None)

        if not entity:
            console.print(f"[red]Entity '{entity_id}' not found.[/red]")
            raise typer.Exit(1)

        console.print(f"[bold]ID:[/bold] {entity.id}")
        console.print(f"[bold]Type:[/bold] {entity.type}")
        console.print(f"[bold]Created:[/bold] {entity.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        console.print(f"[bold]Content:[/bold]\n{entity.content}")
        if entity.metadata:
            console.print(f"[bold]Metadata:[/bold]\n{json.dumps(entity.metadata, indent=2)}")

    except NamespaceNotFoundException:
        console.print(f"[red]Namespace '{namespace}' not found.[/red]")
        raise typer.Exit(1)


# =============================================================================
# Sync Commands
# =============================================================================


@sync_app.command("phoenix")
def sync_phoenix(
    phoenix_url: Annotated[Optional[str], typer.Option("--url", "-u", help="Phoenix server URL")] = None,
    namespace: Annotated[Optional[str], typer.Option("--namespace", "-n", help="Target namespace")] = None,
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Phoenix project name")] = None,
    limit: Annotated[int, typer.Option(help="Maximum number of spans to fetch")] = 100,
    include_errors: Annotated[bool, typer.Option("--include-errors", help="Include failed/error spans")] = False,
):
    """Sync trajectories from Arize Phoenix and generate tips."""
    from kaizen.sync.phoenix_sync import PhoenixSync

    syncer = PhoenixSync(
        phoenix_url=phoenix_url,
        namespace_id=namespace,
        project=project,
    )

    console.print("[bold]Syncing from Phoenix[/bold]")
    console.print(f"  URL: {syncer.phoenix_url}")
    console.print(f"  Project: {syncer.project}")
    console.print(f"  Namespace: {syncer.namespace_id}")
    console.print(f"  Limit: {limit}")
    console.print()

    try:
        result = syncer.sync(limit=limit, include_errors=include_errors)

        table = Table(title="Sync Results")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right")

        table.add_row("Trajectories processed", str(result.processed))
        table.add_row("Trajectories skipped (already synced)", str(result.skipped))
        table.add_row("Tips generated", str(result.tips_generated))
        table.add_row("Errors", str(len(result.errors)))

        console.print(table)

        if result.errors:
            console.print("\n[red]Errors:[/red]")
            for error in result.errors:
                console.print(f"  - {error}")

    except Exception as e:
        console.print(f"[red]Sync failed: {e}[/red]")
        raise typer.Exit(1)


# =============================================================================
# Skills Commands
# =============================================================================


@skills_app.command("package")
def package_skills(
    source: Annotated[Path, typer.Option("--source", "-s", help="Source skills directory")] = Path("plugins/kaizen/skills"),
    output: Annotated[Path, typer.Option("--output", "-o", help="Output directory for .skill files")] = Path("dist"),
    clean: Annotated[bool, typer.Option("--clean", help="Remove existing .skill files before packaging")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be packaged without creating files")] = False,
):
    """Package plugin skills into .skill files for distribution."""
    # Validate source directory
    if not source.exists():
        console.print(f"[red]Source directory not found: {source}[/red]")
        raise typer.Exit(1)

    if not source.is_dir():
        console.print(f"[red]Source is not a directory: {source}[/red]")
        raise typer.Exit(1)

    # Find valid skill directories (those containing SKILL.md)
    skill_dirs: list[tuple[str, Path]] = []
    for item in sorted(source.iterdir()):
        if item.is_dir():
            skill_md = item / "SKILL.md"
            if skill_md.exists():
                skill_dirs.append((item.name, item))

    if not skill_dirs:
        console.print(f"[yellow]No skills found in {source}[/yellow]")
        console.print("[dim]Skills must contain a SKILL.md file[/dim]")
        raise typer.Exit(0)

    # Display found skills
    console.print(f"[bold]Found {len(skill_dirs)} skill(s) in {source}[/bold]\n")

    table = Table(title="Skills to Package")
    table.add_column("Skill", style="cyan")
    table.add_column("Files", justify="right")
    table.add_column("Output", style="dim")

    for skill_name, skill_path in skill_dirs:
        file_count = sum(1 for _ in skill_path.rglob("*") if _.is_file())
        output_file = output / f"{skill_name}.skill"
        table.add_row(skill_name, str(file_count), str(output_file))

    console.print(table)
    console.print()

    if dry_run:
        console.print("[yellow]Dry run - no files created[/yellow]")
        return

    # Create output directory if needed
    output.mkdir(parents=True, exist_ok=True)

    # Clean existing .skill files if requested
    if clean:
        existing_skills = list(output.glob("*.skill"))
        if existing_skills:
            console.print(f"[dim]Removing {len(existing_skills)} existing .skill file(s)...[/dim]")
            for skill_file in existing_skills:
                skill_file.unlink()

    # Package each skill
    packaged = 0
    failed = 0
    for skill_name, skill_path in skill_dirs:
        output_file = output / f"{skill_name}.skill"

        try:
            with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in skill_path.rglob("*"):
                    if file_path.is_file():
                        # Archive path includes skill name as top-level directory
                        arcname = f"{skill_name}/{file_path.relative_to(skill_path)}"
                        zf.write(file_path, arcname)

            console.print(f"[green]Packaged:[/green] {skill_name} -> {output_file}")
            packaged += 1

        except (OSError, PermissionError, zipfile.LargeZipFile, zipfile.BadZipFile, ValueError) as e:
            console.print(f"[red]Failed to package {skill_name}: {e}[/red]")
            failed += 1

    if failed == 0:
        console.print(f"\n[bold green]Successfully packaged {packaged}/{len(skill_dirs)} skill(s)[/bold green]")
    else:
        console.print(f"\n[bold yellow]Packaged {packaged}/{len(skill_dirs)} skill(s); {failed} failed[/bold yellow]")
        sys.exit(1)


if __name__ == "__main__":
    app()
