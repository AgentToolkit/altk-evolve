"""Evolve CLI for managing entities and namespaces."""

import importlib.resources
import json
import platform
import sys
import zipfile
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import Entity
from altk_evolve.schema.exceptions import (
    EvolveException,
    NamespaceAlreadyExistsException,
    NamespaceNotFoundException,
)

app = typer.Typer(help="Evolve CLI - Manage entities and namespaces")
namespaces_app = typer.Typer(help="Namespace management commands")
entities_app = typer.Typer(help="Entity management commands")
sync_app = typer.Typer(help="Sync commands")
skills_app = typer.Typer(help="Skill management commands")
viz_app = typer.Typer(help="Visualization commands")
hooks_app = typer.Typer(help="Hook seam management commands")
retention_app = typer.Typer(help="Data retention commands")

app.add_typer(namespaces_app, name="namespaces")
app.add_typer(entities_app, name="entities")
app.add_typer(sync_app, name="sync")
app.add_typer(skills_app, name="skills")
app.add_typer(viz_app, name="viz")
app.add_typer(hooks_app, name="hooks")
app.add_typer(retention_app, name="retention")

console = Console()


def get_client() -> EvolveClient:
    """Get a EvolveClient instance."""
    return EvolveClient()


# =============================================================================
# Retention Commands
# =============================================================================


@retention_app.command("run")
def run_retention(
    policy_file: Annotated[str, typer.Option("--policy", "-p", help="Path to a retention policy file (YAML or JSON).")],
    namespace: Annotated[Optional[str], typer.Argument(help="Namespace to sweep. Defaults to the configured namespace.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Actually flag/delete. Without this flag the run is a dry run.")] = False,
):
    """Apply a data-retention policy to a namespace.

    Dry run by default - pass --apply to mutate. Reports what was (or would be)
    flagged or deleted, why, and which rule decided it - including memories
    cascade-deleted via session provenance.
    """
    from altk_evolve.retention import RetentionEngine, RetentionPolicy

    client = get_client()
    namespace_id = namespace or client.config.namespace_id

    try:
        policy = RetentionPolicy.from_file(policy_file)
    except FileNotFoundError:
        console.print(f"[red]Policy file not found:[/red] {policy_file}")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Failed to load policy:[/red] {exc}")
        raise typer.Exit(1)

    if not policy.rules:
        console.print(f"[yellow]Policy {policy_file} has no rules; nothing to do.[/yellow]")
        return

    report = RetentionEngine(client).apply(namespace_id, policy, dry_run=not apply)

    mode = "[yellow]DRY RUN[/yellow]" if report.dry_run else "[green]APPLIED[/green]"
    console.print(f"Retention {mode} on namespace [cyan]{namespace_id}[/cyan] - {report.summary()}")

    if report.flagged or report.deleted:
        table = Table(title="Retention actions")
        table.add_column("Action", style="bold")
        table.add_column("Entity ID", style="cyan")
        table.add_column("Type")
        table.add_column("Reason", style="dim")
        table.add_column("Rule", style="dim")
        table.add_column("Why", style="dim")
        for item in [*report.deleted, *report.flagged]:
            verb = "[red]delete[/red]" if item.action == "delete" else "[yellow]flag[/yellow]"
            table.add_row(verb, item.entity_id, item.entity_type, item.reason, item.rule, item.detail)
        console.print(table)
    else:
        console.print("[dim]No entities matched any rule.[/dim]")

    if report.skipped:
        skipped_table = Table(title="Skipped (degraded signal — not acted on)")
        skipped_table.add_column("Entity ID", style="cyan")
        skipped_table.add_column("Type")
        skipped_table.add_column("Reason", style="dim")
        skipped_table.add_column("Rule", style="dim")
        skipped_table.add_column("Why", style="dim")
        for item in report.skipped:
            skipped_table.add_row(item.entity_id, item.entity_type, item.reason, item.rule, item.detail)
        console.print(skipped_table)

    for warning in report.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    for err in report.errors:
        console.print(f"[red]error:[/red] {err}")

    if report.dry_run and (report.flagged or report.deleted):
        console.print("[dim]Dry run - nothing was changed. Re-run with --apply to enforce.[/dim]")

    if report.errors:
        raise typer.Exit(1)


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
    except EvolveException as e:
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
    except EvolveException as e:
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


@entities_app.command("consolidate")
def consolidate_entities(
    namespace: Annotated[str, typer.Argument(help="Namespace to consolidate entities in")],
    threshold: Annotated[Optional[float], typer.Option("--threshold", "-t", help="Cosine similarity threshold (0-1)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show clusters without modifying anything")] = True,
):
    """Cluster similar guideline entities by task description similarity."""
    from altk_evolve.config.evolve import evolve_config

    client = get_client()

    effective_threshold = threshold if threshold is not None else evolve_config.clustering_threshold

    console.print(f"[bold]Clustering entities in '{namespace}'[/bold]")
    console.print(f"  Threshold: {effective_threshold}")
    console.print(f"  Dry run: {dry_run}")
    console.print()

    try:
        clusters = client.cluster_guidelines(namespace, threshold=effective_threshold)
    except NamespaceNotFoundException:
        console.print(f"[red]Namespace '{namespace}' not found.[/red]")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Clustering unavailable:[/red] {e}")
        console.print("[yellow]Configure the embedding model/backend before clustering guidelines.[/yellow]")
        raise typer.Exit(1)

    if not clusters:
        console.print("[yellow]No clusters found. Guidelines have dissimilar task descriptions.[/yellow]")
        return

    console.print(f"[green]Found {len(clusters)} cluster(s)[/green]\n")

    for i, cluster in enumerate(clusters, 1):
        table = Table(title=f"Cluster {i} ({len(cluster)} entities)")
        table.add_column("ID", style="cyan", max_width=20)
        table.add_column("Task Description", max_width=40)
        table.add_column("Content", max_width=50)

        for entity in cluster:
            task_desc = (entity.metadata or {}).get("task_description", "")
            if len(task_desc) > 40:
                task_desc = task_desc[:37] + "..."
            content_str = str(entity.content)
            if len(content_str) > 50:
                content_str = content_str[:47] + "..."
            table.add_row(str(entity.id), task_desc, content_str)

        console.print(table)
        console.print()

    total_entities = sum(len(c) for c in clusters)
    console.print(f"[dim]Total: {total_entities} entities in {len(clusters)} clusters[/dim]")

    if dry_run:
        console.print("\n[yellow]Dry run — no changes made. Use --no-dry-run to consolidate.[/yellow]")
        return

    console.print("\n[bold]Consolidating clusters...[/bold]")
    try:
        result = client.consolidate_guidelines(namespace, threshold=effective_threshold)
        console.print("[green]Consolidation complete:[/green]")
        console.print(f"  Clusters combined: {result.clusters_found}")
        console.print(f"  Guidelines before: {result.guidelines_before}")
        console.print(f"  Guidelines after: {result.guidelines_after}")
    except EvolveException as e:
        console.print(f"[red]Consolidation failed: {e}[/red]")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Consolidation unavailable:[/red] {e}")
        console.print("[yellow]Configure the embedding model/backend before consolidating guidelines.[/yellow]")
        raise typer.Exit(1)


@entities_app.command("select")
def select_entities(
    namespace: Annotated[str, typer.Argument(help="Namespace to select guidelines from")],
    task: Annotated[str, typer.Option("--task", "-q", help="Task instruction to retrieve guidelines for")],
    top_k: Annotated[Optional[int], typer.Option("--top-k", "-k", help="Max task-specific guidelines beyond the core")] = None,
    core_support: Annotated[Optional[int], typer.Option("--core-support", "-c", help="Support threshold for the always-on core")] = None,
):
    """Select an always-on core plus the top-k task-relevant guidelines (dosage-aware retrieval)."""
    client = get_client()

    try:
        selection = client.select_guidelines(namespace, task, top_k=top_k, core_support=core_support)
    except NamespaceNotFoundException:
        console.print(f"[red]Namespace '{namespace}' not found.[/red]")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Retrieval unavailable:[/red] {e}")
        console.print("[yellow]Configure the embedding model/backend before selecting guidelines.[/yellow]")
        raise typer.Exit(1)

    if not selection.all:
        console.print("[yellow]No guidelines found for this task.[/yellow]")
        return

    console.print(f"[bold]Core ({len(selection.core)}):[/bold]")
    for entity in selection.core:
        console.print(f"  • {entity.content}")
    console.print(f"\n[bold]Retrieved ({len(selection.retrieved)}):[/bold]")
    for entity in selection.retrieved:
        console.print(f"  • {entity.content}")
    console.print(
        f"\n[dim]Total: {len(selection.all)} guidelines ({len(selection.core)} core + {len(selection.retrieved)} retrieved)[/dim]"
    )


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
    guidelines_mode: Annotated[
        Optional[str],
        typer.Option("--guidelines-mode", help="Guideline generation mode: regular, consistency, or both"),
    ] = None,
):
    """Sync trajectories from Arize Phoenix and generate guidelines."""
    from altk_evolve.config.guidelines import guidelines_settings
    from altk_evolve.sync.phoenix_sync import PhoenixSync

    if guidelines_mode is not None:
        if guidelines_mode not in ("regular", "consistency", "both"):
            console.print(f"[red]Invalid --guidelines-mode '{guidelines_mode}'. Choose: regular, consistency, both.[/red]")
            raise typer.Exit(1)
        guidelines_settings.guidelines_mode = guidelines_mode

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
    console.print(f"  Guidelines mode: {guidelines_settings.guidelines_mode}")
    console.print()

    try:
        result = syncer.sync(limit=limit, include_errors=include_errors)

        table = Table(title="Sync Results")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right")

        table.add_row("Trajectories processed", str(result.processed))
        table.add_row("Trajectories skipped (already synced)", str(result.skipped))
        table.add_row("Guidelines generated", str(result.guidelines_generated))
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
    source: Annotated[Path, typer.Option("--source", "-s", help="Source skills directory")] = Path("plugins/evolve/skills"),
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


# =============================================================================
# Viz Commands
# =============================================================================


@viz_app.command("serve")
def serve_viz(
    evolve_dir: Annotated[Path, typer.Option("--evolve-dir", "-d", help="Path to .evolve directory")] = Path(".evolve"),
    port: Annotated[int, typer.Option("--port", "-p", help="Port to serve on")] = 7891,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Don't open browser automatically")] = False,
):
    """Serve the Evolve Viz web interface for browsing entities and trajectories."""
    from altk_evolve.viz.server import serve

    serve(evolve_dir=evolve_dir.resolve(), port=port, open_browser=not no_browser)


# =============================================================================
# Hooks Commands
# =============================================================================


def _load_hooks_template() -> str:
    """Read the bundled default hooks config template (READI active, regex
    commented). Packaged as data so `evolve hooks init` works from an install."""
    return importlib.resources.files("altk_evolve.cli.templates").joinpath("hooks.yaml").read_text(encoding="utf-8")


def hooks_init_platform_note(system: str) -> str:
    """Platform-specific guidance printed after `evolve hooks init`.

    ``system`` is ``platform.system()`` (e.g. "Darwin", "Linux", "Windows").
    Kept as a pure function so the macOS vs non-macOS message can be unit-tested
    without spoofing the host OS.
    """
    if system == "Darwin":
        return (
            "macOS note: READI's transformer model runs on Apple-Silicon MPS, which binds to the "
            "first thread that touches it. The hook seam dispatches on a worker thread when an event "
            "loop is already running, so the model can raise 'Placeholder storage has not been "
            "allocated on MPS device!' and — because it is fail-closed (on_error: fail) — BLOCK writes. "
            "For local dev on macOS, uncomment the regex block (and comment READI), or run READI on "
            "CPU/Linux. See docs/guides/pii-redaction.md 'Known limitations'."
        )
    return "Once '[pii-semantic]' is installed, READI works out of the box (weights download on first use)."


@hooks_app.command("init")
def hooks_init(
    path: Annotated[Path, typer.Option("--path", "-p", help="Where to write the hooks config")] = Path("evolve.hooks.yaml"),
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite an existing file")] = False,
):
    """Scaffold a default hooks config (`./evolve.hooks.yaml`).

    The scaffolded file ships the READI SEMANTIC PII plugin ACTIVE and the regex
    PII plugin commented out (both `mode: sequential`, `on_error: fail`). Evolve
    auto-discovers `./evolve.hooks.yaml`, so no further wiring is needed.
    """
    if path.exists() and not force:
        console.print(f"[red]Refusing to overwrite existing file:[/red] {path}")
        console.print("[yellow]Pass --force to overwrite.[/yellow]")
        raise typer.Exit(1)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_load_hooks_template(), encoding="utf-8")
    except OSError as e:
        console.print(f"[red]Could not write {path}: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Wrote hooks config:[/green] {path}")
    console.print("[dim]Evolve auto-discovers ./evolve.hooks.yaml — no further wiring needed.[/dim]\n")
    console.print("[bold]READI semantic PII redaction is enabled by default.[/bold] Install it with:")
    # markup=False so the "[pii-semantic]" extra is printed literally, not eaten
    # as rich markup.
    console.print("  pip install 'altk-evolve[pii-semantic]'", markup=False)
    console.print("[dim](the NER model downloads on first use, ~460MB for en_core_web_trf)[/dim]\n")
    console.print(hooks_init_platform_note(platform.system()), style="yellow", markup=False)


if __name__ == "__main__":
    app()
