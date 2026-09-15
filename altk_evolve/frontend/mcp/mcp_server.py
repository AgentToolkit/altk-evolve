"""
Evolve MCP Server

This server provides a tool to get task-relevant guidelines.
"""

import base64
import datetime
import json
import logging
import threading
import uuid
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml
from fastmcp import FastMCP
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from starlette.requests import Request
from starlette.exceptions import HTTPException
from altk_evolve.config.evolve import evolve_config
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.frontend.services.context import injected_client
from altk_evolve.frontend.api.routes import router as api_router
from altk_evolve.llm.fact_extraction.fact_extraction import (
    ExtractedFact,
    categorize_facts,
    extract_facts_from_messages,
)
from altk_evolve.llm.guidelines.guidelines import generate_guidelines
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity, RecordedEntity
from altk_evolve.schema.exceptions import EvolveException, NamespaceNotFoundException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("entities-mcp")

_client = None
_initialized_namespaces: set[str] = set()
_client_init_lock = threading.Lock()

# Need to configure FastAPI separately and mount FastMCP on it
app = FastAPI(title="Evolve API & UI")
mcp = FastMCP("entities")

# Mount API routes
app.include_router(api_router, prefix="/api")


# Configure UI Static Files Serving
def _setup_ui_routes():
    # UI directory path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_dir = os.path.dirname(current_dir)
    ui_dist_dir = os.path.join(frontend_dir, "ui", "dist")

    # Only mount UI if dist folder exists (i.e. we built it)
    if os.path.exists(ui_dist_dir) and os.path.isdir(ui_dist_dir):
        logger.info(f"Mounting Evolve UI at /ui from {ui_dist_dir}")

        # We mount static files under /ui/assets or similar, but Vite normally
        # places them in dist/assets.
        # For a standard Vite build, index.html is at dist/index.html

        # Mount the entire dist folder at /ui_static
        # Actually in Vite, assets are referenced as /assets/... from index.html
        # We need to mount the assets folder directly at /assets so the browser finds them
        assets_dir = os.path.join(ui_dist_dir, "assets")
        if os.path.exists(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="ui_assets")

        # We can also mount the root dist at /ui_static just in case
        app.mount("/ui_static", StaticFiles(directory=ui_dist_dir), name="ui_static")

        @app.get("/")
        async def root_redirect():
            return RedirectResponse(url="/ui/")

        # Catch-all route to serve the React SPA index.html for /ui and /ui/*
        @app.get("/ui")
        @app.get("/ui/{catchall:path}")
        async def serve_spa(request: Request, catchall: str = ""):
            resolved_base = os.path.realpath(ui_dist_dir)
            # If the requested file exists in dist, serve it (for assets not caught by /ui_static if any)
            if catchall:
                potential_file = os.path.realpath(os.path.join(ui_dist_dir, catchall))
                if potential_file.startswith(resolved_base + os.sep) and os.path.isfile(potential_file):
                    return FileResponse(potential_file)

            # Otherwise serve index.html
            index_file = os.path.realpath(os.path.join(ui_dist_dir, "index.html"))
            if index_file.startswith(resolved_base + os.sep) and os.path.exists(index_file):
                return FileResponse(index_file)
            raise HTTPException(status_code=404, detail="UI index.html not found")
    else:
        logger.info("Evolve UI dist directory not found. Skipping UI mount.")


_setup_ui_routes()


def get_client() -> EvolveClient:
    """Get the EvolveClient singleton with lazy initialization.

    Initializes the client and ensures the default namespace exists on first access.
    This avoids the FastMCP SSE lifespan initialization race condition.
    """
    global _client

    if (client := injected_client.get()) is not None:
        return client

    with _client_init_lock:
        if _client is None:
            logger.info("Initializing Evolve client...")
            _client = EvolveClient()
            logger.info("Evolve client initialized")

        default_ns = evolve_config.namespace_id
        if default_ns not in _initialized_namespaces:
            logger.info(f"Ensuring default namespace '{default_ns}' exists...")
            try:
                _client.ensure_namespace(default_ns)
                _initialized_namespaces.add(default_ns)
                logger.info(f"Namespace '{default_ns}' is ready")
            except Exception as e:
                logger.error(f"Failed to ensure namespace '{default_ns}': {e}")
                raise

        return _client


def _resolve_namespace(namespace_id: str | None) -> str:
    """Resolve the effective namespace, ensuring it exists before use."""
    client = get_client()
    resolved = namespace_id or evolve_config.namespace_id
    if injected_client.get() is not None:
        client.ensure_namespace(resolved)
        return resolved
    if resolved not in _initialized_namespaces:
        logger.info(f"Ensuring namespace '{resolved}' exists (first use)...")
        try:
            client.ensure_namespace(resolved)
            _initialized_namespaces.add(resolved)
            logger.info(f"Namespace '{resolved}' is ready")
        except Exception as e:
            logger.error(f"Failed to ensure namespace '{resolved}': {e}")
            raise
    return resolved


def _evict_namespace(namespace_id: str) -> None:
    """Evict a namespace from the initialization cache.

    Call this when a downstream operation raises NamespaceNotFoundException
    for a namespace that was previously cached — the namespace was likely
    deleted externally.  The next call to _resolve_namespace will
    re-run ensure_namespace to recreate it.
    """
    if namespace_id in _initialized_namespaces:
        _initialized_namespaces.discard(namespace_id)
        logger.info(f"Evicted namespace '{namespace_id}' from cache")


def get_entities_logic(
    task: str,
    entity_type: str = "guideline",
    include_public: bool = False,
    limit: int = 10,
    user_id: str | None = None,
    namespace_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Implementation logic for get_entities tool.

    Retrieval is intentionally broad: user_id and session_id are NOT used as
    hard filters so that shared/older guidelines remain visible.  They are
    accepted here for future opt-in narrowing but currently only logged.
    """
    resolved_ns = _resolve_namespace(namespace_id)
    logger.info(
        f"Getting entities of type '{entity_type}' for task: {task} "
        f"(namespace={resolved_ns}, user_present={user_id is not None}, session_present={session_id is not None}, include_public={include_public})"
    )
    logger.debug(f"get_entities_logic identifiers: user_id={user_id}, session_id={session_id}")
    client = get_client()

    try:
        private_results = client.search_entities(
            namespace_id=resolved_ns,
            query=task,
            filters={"type": entity_type},
            limit=limit,
        )
    except NamespaceNotFoundException:
        _evict_namespace(resolved_ns)
        resolved_ns = _resolve_namespace(namespace_id)
        private_results = client.search_entities(
            namespace_id=resolved_ns,
            query=task,
            filters={"type": entity_type},
            limit=limit,
        )

    header = f"# {entity_type.capitalize()}s for: {task}"
    response_lines = [f"{header}\n"]

    for i, entity in enumerate(private_results, 1):
        response_lines.append(f"{i}. {entity.content}")

    if include_public:
        public_results = client.get_public_entities(
            query=task,
            entity_type=entity_type,
            exclude_namespace_ids=[resolved_ns],
            limit=limit,
        )
        private_ids: set[str] = {e.id for e in private_results}
        seen_public_ids: set[str] = set()
        idx = len(private_results) + 1
        for entity in public_results:
            if entity.id in private_ids or entity.id in seen_public_ids:
                continue
            seen_public_ids.add(entity.id)
            owner = (entity.metadata or {}).get("owner_id", "unknown")
            response_lines.append(f"{idx}. [public: {owner}] {entity.content}")
            idx += 1

    return "\n".join(response_lines)


def _parse_metadata(metadata: str | None) -> dict[str, Any]:
    if not metadata:
        return {}

    try:
        parsed = json.loads(metadata)
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON in metadata parameter: %s", e)
        raise ValueError(f"Failed to parse metadata: {str(e)}") from e

    if not isinstance(parsed, dict):
        raise ValueError("Metadata must decode to a JSON object")

    return parsed


def _json_response(payload: Any) -> str:
    """Serialize MCP responses consistently, including datetimes and errors."""
    return json.dumps(payload, default=str)


def _entity_payload(entity: RecordedEntity, *, include_content: bool = True) -> dict[str, Any]:
    content = entity.content
    preview_source = content if isinstance(content, str) else _json_response(content)
    payload: dict[str, Any] = {
        "id": entity.id,
        "type": entity.type,
        "content_preview": preview_source[:240],
        "created_at": entity.created_at.isoformat(),
        "metadata": entity.metadata or {},
    }
    if include_content:
        payload["content"] = content
    return payload


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        offset = int(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if offset < 0:
        raise ValueError("cursor is invalid")
    return offset


def _parse_datetime(value: str | None, *, field_name: str) -> datetime.datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed


def _entity_owned_by(
    entity: RecordedEntity,
    user_id: str | None,
    agent_id: str | None = None,
) -> bool:
    metadata = entity.metadata or {}
    attributed_ids = {str(value) for value in (metadata.get("owner_id"), metadata.get("user_id")) if value}
    if user_id is None and agent_id is None and (attributed_ids or metadata.get("agent_id")):
        return False
    if user_id is not None:
        if user_id not in attributed_ids:
            return False
    if agent_id is not None and str(metadata.get("agent_id") or "") != agent_id:
        return False
    return True


def _record_entity_access(client: EvolveClient, namespace_id: str, entities: list[RecordedEntity]) -> list[RecordedEntity]:
    if not entities:
        return entities
    moment = datetime.datetime.now(datetime.UTC)
    updated_ids = set(client.record_access(namespace_id, [entity.id for entity in entities], when=moment))
    stamp = moment.isoformat()
    return [
        entity.model_copy(update={"metadata": {**(entity.metadata or {}), "last_accessed": stamp}}) if entity.id in updated_ids else entity
        for entity in entities
    ]


def _persist_entities(
    namespace_id: str | None,
    entities: list[Entity],
    enable_conflict_resolution: bool = False,
) -> tuple[list[EntityUpdate], str]:
    """Persist entities with a single retry if the namespace cache is stale.

    Resolves ``namespace_id`` (falling back to the configured default), writes
    via ``update_entities``, and on ``NamespaceNotFoundException`` evicts the
    cached entry, re-resolves, and retries once. Returns the update records
    and the namespace actually written to.
    """
    resolved_ns = _resolve_namespace(namespace_id)
    try:
        updates = get_client().update_entities(
            namespace_id=resolved_ns,
            entities=entities,
            enable_conflict_resolution=enable_conflict_resolution,
        )
    except NamespaceNotFoundException:
        _evict_namespace(resolved_ns)
        resolved_ns = _resolve_namespace(namespace_id)
        updates = get_client().update_entities(
            namespace_id=resolved_ns,
            entities=entities,
            enable_conflict_resolution=enable_conflict_resolution,
        )
    return updates, resolved_ns


@mcp.tool()
def get_entities(
    task: str,
    entity_type: str = "guideline",
    include_public: bool = False,
    limit: int = 10,
    user_id: str | None = None,
    namespace_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """
    Get relevant entities for a given task, filtered by type.
    Provide a task description and receive applicable best practices, guidelines, or policies.

    Args:
        task: A description of the task you want entities for
        entity_type: The type of entities to retrieve (e.g., 'guideline', 'policy'). Defaults to 'guideline'.
        include_public: If True, also include public entities from all namespaces. Defaults to False.
        limit: Maximum number of results to return from each source (private and public). Defaults to 10.
        user_id: Optional caller user ID. Logged for attribution; does not filter results.
        namespace_id: Optional namespace override. Falls back to the configured default.
        session_id: Optional session/thread ID. Logged for attribution; does not filter results.
    """
    return get_entities_logic(task, entity_type, include_public, limit, user_id, namespace_id, session_id)


@mcp.tool()
def get_guidelines(
    task: str,
    user_id: str | None = None,
    namespace_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """
    Get relevant guidelines for a given task.
    Provide a task description and receive applicable best practices and guidelines.
    This tool is maintained for backward compatibility. Use 'get_entities' for more generic queries.

    Honors ``EvolveConfig.injection_mode``: 'static' (default) returns the whole guideline set,
    while 'retrieval' routes through the dosage-aware core + top-k path (see get_relevant_guidelines).

    Args:
        task: A description of the task you want guidelines for
        user_id: Optional caller user ID. Logged for attribution; does not filter results.
        namespace_id: Optional namespace override. Falls back to the configured default.
        session_id: Optional session/thread ID. Logged for attribution; does not filter results.
    """
    from altk_evolve.config.evolve import evolve_config

    if evolve_config.injection_mode == "retrieval":
        return get_relevant_guidelines(task, user_id=user_id, namespace_id=namespace_id, session_id=session_id)
    return get_entities_logic(task, "guideline", user_id=user_id, namespace_id=namespace_id, session_id=session_id)


@mcp.tool()
def get_guidelines_with_attribution(
    task: str,
    user_id: str | None = None,
    namespace_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Return injectable guidelines together with the entities that supplied them.

    Existing callers keep the text-only contract; clients that need to audit
    actual prompt context can record ``entity_ids`` as memory-use events.
    """
    from altk_evolve.config.evolve import evolve_config
    from altk_evolve.llm.guidelines.retrieval import format_selection

    resolved_ns = _resolve_namespace(namespace_id)
    client = get_client()
    if evolve_config.injection_mode == "retrieval":
        try:
            selection = client.select_guidelines(resolved_ns, task)
        except NamespaceNotFoundException:
            _evict_namespace(resolved_ns)
            resolved_ns = _resolve_namespace(namespace_id)
            selection = client.select_guidelines(resolved_ns, task)
        entities = selection.all
        text = format_selection(selection)
    else:
        try:
            entities = client.search_entities(
                namespace_id=resolved_ns,
                query=task,
                filters={"type": "guideline"},
                limit=10,
            )
        except NamespaceNotFoundException:
            _evict_namespace(resolved_ns)
            resolved_ns = _resolve_namespace(namespace_id)
            entities = client.search_entities(
                namespace_id=resolved_ns,
                query=task,
                filters={"type": "guideline"},
                limit=10,
            )
        lines = [f"# Guidelines for: {task}\n"]
        lines.extend(f"{index}. {entity.content}" for index, entity in enumerate(entities, 1))
        text = "\n".join(lines)

    logger.info(
        "get_guidelines_with_attribution (namespace=%s, count=%s, user_present=%s, session_present=%s)",
        resolved_ns,
        len(entities),
        user_id is not None,
        session_id is not None,
    )
    return _json_response(
        {
            "text": text,
            "entity_ids": [entity.id for entity in entities],
            "namespace_id": resolved_ns,
        }
    )


@mcp.tool()
def get_relevant_guidelines(
    task: str,
    top_k: int | None = None,
    core_support: int | None = None,
    namespace_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """
    Get a dosage-aware set of guidelines for a task: an always-on core plus the top-k
    guidelines most relevant to this task (retrieved by similarity to the tasks they were
    learned from).

    Prefer this over 'get_guidelines'/'get_entities' for weaker models, where injecting the
    whole playbook can hurt and a small, targeted set helps.

    Args:
        task: A description of the task you want guidelines for.
        top_k: Max task-specific guidelines to add beyond the core. Defaults to config.
        core_support: Support threshold for the always-on core. Defaults to config.
        namespace_id: Optional namespace override. Falls back to the configured default.
        user_id: Optional caller user ID. Logged for attribution; does not filter results.
        session_id: Optional session/thread ID. Logged for attribution; does not filter results.
    """
    from altk_evolve.llm.guidelines.retrieval import format_selection

    resolved_ns = _resolve_namespace(namespace_id)
    # Log only non-sensitive metadata at INFO; task is arbitrary user text (see save_trajectory).
    logger.info(
        "get_relevant_guidelines (namespace=%s, top_k=%s, core_support=%s, task_len=%s, user_present=%s, session_present=%s)",
        resolved_ns,
        top_k,
        core_support,
        len(task),
        user_id is not None,
        session_id is not None,
    )
    logger.debug("get_relevant_guidelines task=%s", task)
    client = get_client()
    try:
        selection = client.select_guidelines(resolved_ns, task, top_k=top_k, core_support=core_support)
    except NamespaceNotFoundException:
        _evict_namespace(resolved_ns)
        resolved_ns = _resolve_namespace(namespace_id)
        selection = client.select_guidelines(resolved_ns, task, top_k=top_k, core_support=core_support)
    except ValueError as e:
        logger.error("Retrieval unavailable for get_relevant_guidelines: %s", e)
        return f"Guideline retrieval unavailable: {e}"
    return format_selection(selection)


@mcp.tool()
def list_entities(
    entity_types: list[str] | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    metadata_filters: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    include_content: bool = False,
    record_access: bool = False,
    namespace_id: str | None = None,
) -> str:
    """Return a structured, paginated entity inventory.

    This is the UI/admin counterpart to ``get_entities``, whose prose response
    is intentionally optimized for prompt injection. Administrative scans do
    not count as memory use by default; set ``record_access=True`` for a
    user-facing read that should refresh ``metadata.last_accessed``.
    """
    try:
        filters = _parse_metadata(metadata_filters)
        offset = _decode_cursor(cursor)
    except ValueError as exc:
        return _json_response({"error": str(exc)})

    resolved_ns = _resolve_namespace(namespace_id)
    page_size = max(1, min(limit, 200))
    scan_limit = 100_000
    client = get_client()
    candidates = client.scan_entities(resolved_ns, limit=scan_limit)

    wanted_types = set(entity_types or [])

    def matches(entity: RecordedEntity) -> bool:
        metadata = entity.metadata or {}
        if wanted_types and entity.type not in wanted_types:
            return False
        if user_id and user_id not in {metadata.get("user_id"), metadata.get("owner_id")}:
            return False
        if agent_id and metadata.get("agent_id") != agent_id:
            return False
        if session_id and session_id not in {metadata.get("session_id"), metadata.get("thread_id")}:
            return False
        return all(metadata.get(key) == value for key, value in filters.items())

    matched = [entity for entity in candidates if matches(entity)]
    matched.sort(key=lambda entity: (entity.created_at, entity.id), reverse=True)
    page = matched[offset : offset + page_size]
    consumed = len(page)
    if record_access and page:
        transformed_page = []
        for entity in page:
            transformed = client.get_entity_by_id(resolved_ns, entity.id)
            if transformed is not None:
                transformed_page.append(transformed)
        page = _record_entity_access(client, resolved_ns, transformed_page)
    next_offset = offset + consumed
    facets: dict[str, int] = {}
    for entity in matched:
        facets[entity.type] = facets.get(entity.type, 0) + 1

    return _json_response(
        {
            "items": [_entity_payload(entity, include_content=include_content) for entity in page],
            "next_cursor": _encode_cursor(next_offset) if next_offset < len(matched) else None,
            "total": len(matched),
            "facets": {"entity_types": facets},
            "truncated": len(candidates) >= scan_limit,
            "namespace_id": resolved_ns,
        }
    )


@mcp.tool()
def get_entity(
    entity_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    record_access: bool = True,
    namespace_id: str | None = None,
) -> str:
    """Return one structured entity, optionally recording a user-facing read."""
    resolved_ns = _resolve_namespace(namespace_id)
    client = get_client()
    matches = client.scan_entities(resolved_ns, filters={"id": entity_id}, limit=1)
    entity = matches[0] if matches else None
    if entity is None:
        return _json_response({"error": f"Entity {entity_id} not found"})
    if not _entity_owned_by(entity, user_id, agent_id):
        return _json_response({"error": "Permission denied: caller is not the owner of this entity"})
    if record_access:
        refreshed = client.get_entity_by_id(resolved_ns, entity_id)
        if refreshed is not None:
            entity = refreshed
        entity = _record_entity_access(client, resolved_ns, [entity])[0]
    return _json_response(_entity_payload(entity))


@mcp.tool()
def patch_entity_metadata(
    entity_id: str,
    metadata_patch: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    namespace_id: str | None = None,
) -> str:
    """Merge metadata into an owned entity through the memory hook seam."""
    try:
        patch = _parse_metadata(metadata_patch)
    except ValueError as exc:
        return _json_response({"error": str(exc)})

    resolved_ns = _resolve_namespace(namespace_id)
    client = get_client()
    matches = client.scan_entities(resolved_ns, filters={"id": entity_id}, limit=1)
    entity = matches[0] if matches else None
    if entity is None:
        return _json_response({"error": f"Entity {entity_id} not found"})
    if not _entity_owned_by(entity, user_id, agent_id):
        return _json_response({"error": "Permission denied: caller is not the owner of this entity"})
    try:
        updated = client.patch_entity_metadata(resolved_ns, entity_id, patch)
        return _json_response(_entity_payload(updated))
    except EvolveException as exc:
        return _json_response({"error": str(exc)})


@mcp.tool()
def record_access(
    entity_ids: list[str],
    accessed_at: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    namespace_id: str | None = None,
) -> str:
    """Explicitly stamp memories as used without requiring a retrieval query."""
    try:
        moment = _parse_datetime(accessed_at, field_name="accessed_at")
    except ValueError as exc:
        return _json_response({"error": str(exc)})

    resolved_ns = _resolve_namespace(namespace_id)
    client = get_client()
    allowed: list[str] = []
    denied: list[str] = []
    missing: list[str] = []
    for entity_id in dict.fromkeys(entity_ids):
        matches = client.scan_entities(resolved_ns, filters={"id": entity_id}, limit=1)
        entity = matches[0] if matches else None
        if entity is None:
            missing.append(entity_id)
        elif not _entity_owned_by(entity, user_id, agent_id):
            denied.append(entity_id)
        else:
            allowed.append(entity_id)

    moment = moment or datetime.datetime.now(datetime.UTC)
    updated = client.record_access(resolved_ns, allowed, when=moment) if allowed else []
    return _json_response(
        {
            "updated_ids": updated,
            "denied_ids": denied,
            "missing_ids": missing,
            "accessed_at": moment.isoformat(),
            "namespace_id": resolved_ns,
        }
    )


def _retention_object(value: dict[str, Any] | str | None) -> dict[str, Any]:
    return value if isinstance(value, dict) else _parse_metadata(value)


@mcp.tool()
def validate_retention_policy(policy: dict[str, Any] | str) -> str:
    """Validate a structured retention policy without scanning data."""
    from altk_evolve.retention.service import RetentionService, RetentionError

    try:
        normalized = RetentionService.validate_policy(_retention_object(policy))
        return _json_response({"valid": True, "normalized_policy": normalized, "errors": [], "warnings": []})
    except (ValueError, RetentionError) as exc:
        return _json_response({"valid": False, "normalized_policy": None, "errors": [{"message": str(exc)}], "warnings": []})


def _retention_store(client: EvolveClient | None = None):
    from altk_evolve.retention.schedule_store import ScheduleStore

    return ScheduleStore(client or get_client())


def _retention_service(namespace_id: str | None, agent_id: str | None = None):
    from altk_evolve.retention.service import RetentionService

    return RetentionService(
        get_client(), namespace_id if namespace_id is not None else evolve_config.namespace_id, agent_id=agent_id, store=_retention_store()
    )


def _retention_call(namespace_id, operation, *args, agent_id=None, **kwargs) -> str:
    from altk_evolve.retention.service import RetentionError

    try:
        return _json_response(getattr(_retention_service(namespace_id, agent_id), operation)(*args, **kwargs))
    except RetentionError as exc:
        return _json_response(exc.payload())


@mcp.tool()
def put_retention_policy(
    policy_id: str,
    name: str,
    policy: dict[str, Any] | str,
    description: str | None = None,
    enabled: bool = True,
    namespace_id: str | None = None,
) -> str:
    """Create or replace the complete policy document in the supplied namespace."""
    try:
        parsed = _retention_object(policy)
    except ValueError as exc:
        return _json_response({"error": "Invalid retention policy", "details": [{"message": str(exc)}]})
    return _retention_call(namespace_id, "put_policy", policy_id, name=name, policy=parsed, description=description, enabled=enabled)


@mcp.tool()
def get_retention_policy(policy_id: str, namespace_id: str | None = None) -> str:
    """Return a namespace-owned policy."""
    return _retention_call(namespace_id, "get_policy", policy_id)


@mcp.tool()
def list_retention_policies(namespace_id: str | None = None, include_disabled: bool = False) -> str:
    """List policies in the supplied namespace."""
    return _retention_call(namespace_id, "list_policies", include_disabled=include_disabled)


@mcp.tool()
def run_retention(
    policy_id: str,
    dry_run: bool = True,
    as_of: str | None = None,
    scan_limit: int | None = None,
    run_id: str | None = None,
    namespace_id: str | None = None,
    metadata_filters: dict[str, Any] | str | None = None,
    additional_matches: list[dict[str, Any]] | str | None = None,
    initiated_by: str | None = None,
) -> str:
    """Execute a stored policy and persist its audit report through the shared retention service."""
    try:
        filters = _retention_object(metadata_filters) if metadata_filters else None
        matches = json.loads(additional_matches) if isinstance(additional_matches, str) else additional_matches
    except ValueError as exc:
        return _json_response({"error": "Invalid retention request", "details": [{"message": str(exc)}]})
    return _retention_call(
        namespace_id,
        "run",
        policy_id,
        dry_run=dry_run,
        as_of=as_of,
        scan_limit=scan_limit,
        run_id=run_id,
        metadata_filters=filters,
        additional_matches=matches,
        initiated_by=initiated_by,
    )


@mcp.tool()
def list_retention_runs(namespace_id: str | None = None, agent_id: str | None = None, policy_id: str | None = None, limit: int = 50) -> str:
    """List persisted retention runs in the requested scope."""
    return _retention_call(namespace_id, "list_runs", agent_id=agent_id, policy_id=policy_id, limit=limit)


def _protection_class(name: str, kind: str, hooks: list[str]) -> str:
    searchable = f"{name} {kind}".lower()
    if "secret" in searchable:
        return "secrets"
    if "pii" in searchable or "redact" in searchable:
        return "pii"
    if "access" in searchable:
        return "access"
    if "normalizer" in searchable or "provenance" in searchable:
        return "provenance"
    if "memory_pre_delete" in hooks:
        return "deletion_protection"
    return "memory_policy"


def _configured_hook_plugins() -> list[dict[str, Any]]:
    from altk_evolve.config.hooks import discover_hooks_config_path

    specs = [spec.model_dump(mode="json") for spec in evolve_config.hooks.plugins]
    yaml_path = evolve_config.hooks.plugins_yaml
    if not yaml_path and not specs:
        yaml_path = discover_hooks_config_path()
    if yaml_path:
        loaded = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"hooks config {yaml_path} must hold a mapping with a 'plugins' list")
        yaml_specs = loaded.get("plugins", []) or []
        if not isinstance(yaml_specs, list) or any(not isinstance(spec, dict) for spec in yaml_specs):
            raise ValueError(f"hooks config {yaml_path} must contain a 'plugins' list of mappings")
        specs = yaml_specs + specs
    return specs


@mcp.tool()
def get_compliance_status(namespace_id: str | None = None) -> str:
    """Report Evolve backend, retention, and configured memory-hook health."""
    from altk_evolve.hooks.manager import get_plugin_manager, hooks_active
    from altk_evolve.hooks.types import HookType, engine_available

    resolved_ns = _resolve_namespace(namespace_id)
    client = get_client()
    manager = get_plugin_manager()
    try:
        specs = _configured_hook_plugins()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return _json_response({"healthy": False, "error": f"Unable to read hook configuration: {exc}"})

    plugins = []
    for spec in specs:
        hooks = list(spec.get("hooks", []) or [])
        mode = str(spec.get("mode", "sequential"))
        enabled = mode != "disabled"
        plugins.append(
            {
                "name": str(spec.get("name") or spec.get("kind") or "unnamed"),
                "kind": str(spec.get("kind") or ""),
                "protection_class": _protection_class(
                    str(spec.get("name") or ""),
                    str(spec.get("kind") or ""),
                    hooks,
                ),
                "hooks": hooks,
                "enabled": enabled,
                "healthy": bool(manager is not None and enabled and all(manager.has_hooks_for(hook) for hook in hooks)),
            }
        )

    try:
        package_version = version("altk-evolve")
    except PackageNotFoundError:
        package_version = "unknown"

    hook_coverage = {hook.value: hooks_active(hook) for hook in HookType}
    hook_engine_available = engine_available()
    retention_available = (callable(getattr(client, "scan_entities", None)) or callable(getattr(client, "get_all_entities", None))) and all(
        callable(getattr(client, method, None)) for method in ("patch_entity_metadata", "delete_entity_by_id")
    )
    any_plugin_enabled = any(plugin["enabled"] for plugin in plugins)
    healthy = (
        client.ready()
        and retention_available
        and (not any_plugin_enabled or hook_engine_available)
        and all(not plugin["enabled"] or plugin["healthy"] for plugin in plugins)
    )
    return _json_response(
        {
            "healthy": healthy,
            "evolve_version": package_version,
            "backend": evolve_config.backend,
            "namespace_id": resolved_ns,
            "retention_available": retention_available,
            "hooks_enabled": manager is not None,
            "hook_engine_available": hook_engine_available,
            "hook_coverage": hook_coverage,
            "plugins": plugins,
        }
    )


def _empty_store_user_facts_response(user_id: str) -> str:
    return json.dumps({"user_id": user_id, "stored_count": 0, "updates": []})


@mcp.tool()
def store_user_facts(
    user_id: str,
    message: str,
    metadata: str | None = None,
    enable_conflict_resolution: bool = False,
    namespace_id: str | None = None,
) -> str:
    """Store personal facts in the supplied namespace (the service instance ID).

    Omitting namespace_id retains the configured default for legacy callers.
    The explicit user_id overrides any user identity supplied in metadata.
    """
    if namespace_id is not None and (not namespace_id.strip() or not user_id.strip()):
        return json.dumps({"error": "namespace_id and user_id must be nonblank"})
    try:
        metadata_dict = _parse_metadata(metadata)
    except ValueError as e:
        return json.dumps(
            {
                "error": "Invalid JSON",
                "message": str(e),
                "invalid_metadata": metadata,
            }
        )

    trimmed_message = (message or "").strip()
    if not trimmed_message:
        return _empty_store_user_facts_response(user_id)

    base_metadata: dict[str, Any] = dict(metadata_dict)
    base_metadata["user_id"] = user_id

    extracted = extract_facts_from_messages([{"role": "user", "content": trimmed_message}])
    entities: list[Entity] = []
    for one in extracted:
        if isinstance(one, ExtractedFact):
            fact_metadata = dict(base_metadata)
            fact_metadata["category"] = one.category
            fact_metadata["key"] = one.key
            fact_metadata["value"] = one.value
            entities.append(Entity(type="fact", content=one.content, metadata=fact_metadata))
        else:
            entities.append(Entity(type="fact", content=str(one), metadata=dict(base_metadata)))

    if not entities:
        return _empty_store_user_facts_response(user_id)

    updates, _ = _persist_entities(
        namespace_id=namespace_id,
        entities=entities,
        enable_conflict_resolution=enable_conflict_resolution,
    )

    serialized_updates = [
        {
            "event": update.event,
            "id": update.id,
            "type": update.type,
            "content": update.content,
            "metadata": update.metadata,
        }
        for update in updates
    ]

    return json.dumps(
        {
            "user_id": user_id,
            "stored_count": len(serialized_updates),
            "updates": serialized_updates,
        }
    )


def _search_facts_with_fallback(
    namespace_id: str,
    user_id: str,
    query: str | None,
    limit: int,
    *,
    allow_default_user: bool = True,
    agent_id: str | None = None,
) -> list[RecordedEntity]:
    """Fetch fact entities for a user with the legacy fallback chain.

    Order: (1) user filter + query, (2) user filter without query, (3) default
    user with query, (4) default user without query. The default-user fallback
    is skipped for explicitly scoped calls or when the caller is already ``"default"``.
    """
    client = get_client()
    agent_filter = {"metadata.agent_id": agent_id} if agent_id is not None else {}
    facts = client.search_entities(
        namespace_id=namespace_id,
        query=query,
        filters={"type": "fact", "metadata.user_id": user_id, **agent_filter},
        limit=limit,
    )
    if query and not facts:
        facts = client.search_entities(
            namespace_id=namespace_id,
            query=None,
            filters={"type": "fact", "metadata.user_id": user_id, **agent_filter},
            limit=limit,
        )
    if allow_default_user and not facts and user_id != "default":
        facts = client.search_entities(
            namespace_id=namespace_id,
            query=query,
            filters={"type": "fact", "metadata.user_id": "default", **agent_filter},
            limit=limit,
        )
        if query and not facts:
            facts = client.search_entities(
                namespace_id=namespace_id,
                query=None,
                filters={"type": "fact", "metadata.user_id": "default", **agent_filter},
                limit=limit,
            )
    return facts


@mcp.tool()
def retrieve_user_facts(
    user_id: str, query: str | None = None, limit: int = 5, namespace_id: str | None = None, agent_id: str | None = None
) -> str:
    """Retrieve facts for the exact namespace/user pair without crossing users.

    Only legacy calls omitting namespace_id retain the default-user fallback.
    Reads of absent namespaces return no facts without creating a namespace.
    """
    if namespace_id is not None and (not namespace_id.strip() or not user_id.strip()):
        return json.dumps({"error": "namespace_id and user_id must be nonblank"})
    allow_default_user = namespace_id is None
    namespace_id = namespace_id if namespace_id is not None else evolve_config.namespace_id

    if limit <= 0 or not get_client().namespace_exists(namespace_id):
        return json.dumps(
            {
                "user_id": user_id,
                "query": query,
                "matched_count": 0,
                "categories": {},
            }
        )

    facts = _search_facts_with_fallback(namespace_id, user_id, query, limit, allow_default_user=allow_default_user, agent_id=agent_id)
    categories = categorize_facts(facts)
    matched_count = sum(len(items) for items in categories.values())

    return json.dumps(
        {
            "user_id": user_id,
            "query": query,
            "matched_count": matched_count,
            "categories": categories,
        }
    )


@mcp.tool()
def save_trajectory(
    trajectory_data: str,
    task_id: str | None = None,
    owner_id: str | None = None,
    user_id: str | None = None,
    namespace_id: str | None = None,
    session_id: str | None = None,
    tools: str | None = None,
    agent_id: str | None = None,
) -> list[RecordedEntity]:
    """
    Save the full agent trajectory to the Entity DB and generate guidelines

    Args:
        trajectory_data: A JSON formatted OpenAI conversation.
        task_id: Optional identifier for the task.
        owner_id: Optional user ID to record as the owner of generated guidelines.
        user_id: Optional caller user ID. Attached as metadata to trajectory and guideline entities.
        namespace_id: Optional namespace override. Falls back to the configured default.
        session_id: Optional session/thread ID. Attached as metadata to trajectory and guideline entities.
        tools: Optional JSON-encoded OpenAI tools schema. When provided, passed to the consistency
            pipeline so tool-calling steps are named and resampled correctly (OpenAIAgent vs AnyAgent).
    """
    from altk_evolve.config.guidelines import guidelines_settings

    guidelines_mode = guidelines_settings.guidelines_mode

    resolved_ns = _resolve_namespace(namespace_id)
    # Prefer explicit user_id; fall back to owner_id for backward compatibility
    effective_user_id = user_id or owner_id
    task_id = task_id or str(uuid.uuid4())

    logger.info(
        f"Saving trajectory: namespace={resolved_ns}, user_present={effective_user_id is not None}, session_present={session_id is not None}, task_id={task_id}"
    )
    logger.debug(f"save_trajectory identifiers: user_id={effective_user_id}, session_id={session_id}")

    entities = []
    messages = json.loads(trajectory_data)
    trajectory_metadata_base: dict = {"task_id": task_id}
    if agent_id:
        trajectory_metadata_base["agent_id"] = agent_id
    if effective_user_id:
        trajectory_metadata_base["user_id"] = effective_user_id
    if session_id:
        trajectory_metadata_base["session_id"] = session_id

    for message in messages:
        entities.append(
            Entity(
                type="trajectory",
                content=message["content"] if isinstance(message["content"], str) else str(message["content"]),
                metadata={
                    **trajectory_metadata_base,
                    "message": message,
                },
            )
        )

    _, resolved_ns = _persist_entities(
        namespace_id=namespace_id,
        entities=entities,
        enable_conflict_resolution=False,
    )

    guideline_metadata_base: dict = {
        "source_task_id": task_id,
        "creation_mode": "auto-mcp",
    }
    if agent_id:
        guideline_metadata_base["agent_id"] = agent_id
    if effective_user_id:
        guideline_metadata_base["owner_id"] = effective_user_id
        guideline_metadata_base["user_id"] = effective_user_id
    if session_id:
        guideline_metadata_base["session_id"] = session_id

    # Build entity lists per pipeline so each carries its own generation_method tag,
    # then merge before the single update_entities call.
    guideline_entities = []

    if guidelines_mode in ("standard", "all"):
        try:
            regular_results = generate_guidelines(messages)
            guideline_entities += [
                Entity(
                    type="guideline",
                    content=guideline.content,
                    metadata={
                        **guideline_metadata_base,
                        "task_description": result.task_description,
                        "category": guideline.category,
                        "rationale": guideline.rationale,
                        "trigger": guideline.trigger,
                        "implementation_steps": guideline.implementation_steps,
                        "generation_method": "standard",
                        "support": 1,
                    },
                )
                for result in regular_results
                for guideline in result.guidelines
            ]
        except Exception:
            logger.error(
                f"Standard guideline generation failed for task {task_id}, skipping",
                exc_info=True,
            )

    if guidelines_mode in ("consistency", "all"):
        try:
            trajectory = {
                "messages": messages,
                "trace_id": task_id,
                "tools": json.loads(tools) if tools else None,
            }
            if guidelines_settings.consistency_method == "fast":
                from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines_fast

                consistency_results = generate_consistency_guidelines_fast(trajectory)
                consistency_method_tag = "consistency-fast"
            else:
                from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines

                consistency_results = generate_consistency_guidelines(trajectory)
                consistency_method_tag = "consistency"
            guideline_entities += [
                Entity(
                    type="guideline",
                    content=guideline.content,
                    metadata={
                        **guideline_metadata_base,
                        "task_description": result.task_description,
                        "category": guideline.category,
                        "rationale": guideline.rationale,
                        "trigger": guideline.trigger,
                        "implementation_steps": guideline.implementation_steps,
                        "generation_method": consistency_method_tag,
                        "support": 1,
                    },
                )
                for result in consistency_results
                for guideline in result.guidelines
            ]
        except Exception:
            logger.error(
                f"Consistency guideline generation failed for task {task_id}, skipping",
                exc_info=True,
            )
    if guideline_entities:
        get_client().update_entities(
            namespace_id=resolved_ns,
            entities=guideline_entities,
            enable_conflict_resolution=True,
        )

    readback_filters: dict = {"type": "trajectory", "metadata.task_id": task_id}
    if effective_user_id:
        readback_filters["metadata.user_id"] = effective_user_id
    if session_id:
        readback_filters["metadata.session_id"] = session_id

    return get_client().search_entities(
        namespace_id=resolved_ns,
        filters=readback_filters,
        limit=1000,
    )


@mcp.tool()
def create_entity(
    content: str,
    entity_type: str,
    metadata: str | None = None,
    enable_conflict_resolution: bool = False,
    owner_id: str | None = None,
    visibility: str = "private",
    namespace_id: str | None = None,
    created_at: str | None = None,
) -> str:
    """
    Create a single entity in the namespace.

    Args:
        content: The searchable text or structured data for the entity
        entity_type: The type/category of the entity (e.g., 'guideline', 'note', 'fact')
        metadata: Optional JSON string containing arbitrary metadata related to the entity
        enable_conflict_resolution: If True, uses LLM to check for conflicts with existing entities
        owner_id: Optional user ID to record as the owner of this entity
        visibility: Visibility of the entity — 'private' (default) or 'public'
        namespace_id: Optional namespace override. Falls back to the configured default.
        created_at: Optional ISO-8601 timestamp for administrative fixture/import data.

    Returns:
        JSON string with the entity update details (ADD/UPDATE/DELETE/NONE) and entity ID
    """
    logger.info(f"Creating entity of type: {entity_type} (namespace override: {namespace_id})")
    try:
        parsed_created_at = None
        if created_at:
            from datetime import UTC, datetime

            try:
                parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                return json.dumps({"error": "Invalid created_at", "message": "created_at must be ISO-8601"})
            if parsed_created_at.tzinfo is None:
                parsed_created_at = parsed_created_at.replace(tzinfo=UTC)
        if visibility not in ("private", "public"):
            return json.dumps({"error": f"Invalid visibility '{visibility}': must be 'private' or 'public'"})
        if visibility == "public" and not owner_id:
            return json.dumps({"error": "Missing owner_id", "message": "public entities must have an owner_id"})

        _RESERVED_KEYS = {"owner_id", "visibility", "published_at", "creation_mode"}

        metadata_dict = {}
        if metadata:
            try:
                metadata_dict = json.loads(metadata)
            except json.JSONDecodeError as e:
                logger.warning("Invalid JSON in metadata parameter: %s", e)
                return json.dumps({"error": "Invalid JSON", "message": f"Failed to parse metadata: {str(e)}", "invalid_metadata": metadata})
            if not isinstance(metadata_dict, dict):
                return json.dumps(
                    {"error": "Invalid metadata type", "message": "metadata must be a JSON object", "invalid_metadata": metadata}
                )
            for key in _RESERVED_KEYS:
                metadata_dict.pop(key, None)

        if entity_type in ("guideline", "policy"):
            metadata_dict.setdefault("creation_mode", "manual")

        metadata_dict["visibility"] = visibility
        if visibility == "public":
            from datetime import UTC, datetime

            metadata_dict.setdefault("published_at", datetime.now(UTC).isoformat())
        if owner_id:
            metadata_dict["owner_id"] = owner_id

        entity = Entity(type=entity_type, content=content, metadata=metadata_dict)

        updates, resolved_ns = _persist_entities(
            namespace_id=namespace_id,
            entities=[entity],
            enable_conflict_resolution=enable_conflict_resolution,
        )

        if updates:
            update = updates[0]
            if parsed_created_at and update.event == "ADD":
                readback = get_client().set_entity_created_at(namespace_id=resolved_ns, entity_id=update.id, created_at=parsed_created_at)
                return json.dumps(
                    {
                        "event": update.event,
                        "id": readback.id,
                        "type": readback.type,
                        "content": readback.content,
                        "metadata": readback.metadata,
                        "created_at": readback.created_at.isoformat(),
                    }
                )
            return json.dumps(
                {
                    "event": update.event,
                    "id": update.id,
                    "type": update.type,
                    "content": update.content,
                    "metadata": update.metadata,
                }
            )
        else:
            return json.dumps({"error": "Entity creation failed"})

    except Exception as e:
        import traceback

        traceback.print_exc()
        logger.exception(f"CRASH IN CREATE_ENTITY: {e}")
        return json.dumps({"error": f"Server Error: {str(e)}"})


@mcp.tool()
def publish_entity(entity_id: str, user_id: str | None = None, namespace_id: str | None = None) -> str:
    """
    Make an entity publicly visible to all users.

    Args:
        entity_id: The ID of the entity to publish
        user_id: Caller identity; must match the entity's owner_id if one is set
        namespace_id: Optional namespace override. Falls back to the configured default.

    Returns:
        JSON string with the updated entity, or an error message
    """
    resolved_ns = _resolve_namespace(namespace_id)
    logger.info(f"publish entity={entity_id} owner_present={user_id is not None} namespace={resolved_ns}")
    try:
        from datetime import datetime, UTC

        entity = get_client().get_entity_by_id(namespace_id=resolved_ns, entity_id=entity_id)
        if entity is None:
            return json.dumps({"error": f"Entity {entity_id} not found"})

        existing_owner = (entity.metadata or {}).get("owner_id")
        if existing_owner is not None and user_id != existing_owner:
            return json.dumps({"error": "Permission denied: caller is not the owner of this entity"})

        metadata_updates: dict = {
            "visibility": "public",
            "published_at": datetime.now(UTC).isoformat(),
        }
        if user_id is not None:
            metadata_updates["owner_id"] = user_id
        updated = get_client().patch_entity_metadata(
            namespace_id=resolved_ns,
            entity_id=entity_id,
            metadata_updates=metadata_updates,
        )
        return json.dumps({"id": updated.id, "type": updated.type, "content": updated.content, "metadata": updated.metadata})
    except NamespaceNotFoundException:
        _evict_namespace(resolved_ns)
        return json.dumps({"error": f"Namespace '{resolved_ns}' not found"})
    except EvolveException as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def unpublish_entity(entity_id: str, user_id: str | None = None, namespace_id: str | None = None) -> str:
    """
    Revert an entity to private visibility.

    Args:
        entity_id: The ID of the entity to unpublish
        user_id: Caller identity; must match the entity's owner_id if one is set
        namespace_id: Optional namespace override. Falls back to the configured default.

    Returns:
        JSON string with the updated entity, or an error message
    """
    resolved_ns = _resolve_namespace(namespace_id)
    logger.info(f"unpublish entity={entity_id} namespace={resolved_ns}")
    try:
        entity = get_client().get_entity_by_id(namespace_id=resolved_ns, entity_id=entity_id)
        if entity is None:
            return json.dumps({"error": f"Entity {entity_id} not found"})

        existing_owner = (entity.metadata or {}).get("owner_id")
        if existing_owner is not None and user_id != existing_owner:
            return json.dumps({"error": "Permission denied: caller is not the owner of this entity"})

        updated = get_client().patch_entity_metadata(
            namespace_id=resolved_ns,
            entity_id=entity_id,
            metadata_updates={"visibility": "private", "published_at": None},
        )
        return json.dumps({"id": updated.id, "type": updated.type, "content": updated.content, "metadata": updated.metadata})
    except NamespaceNotFoundException:
        _evict_namespace(resolved_ns)
        return json.dumps({"error": f"Namespace '{resolved_ns}' not found"})
    except EvolveException as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def delete_entity(
    entity_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    namespace_id: str | None = None,
) -> str:
    """
    Delete a specific entity by its ID.

    Args:
        entity_id: The unique identifier of the entity to delete
        user_id: Caller identity; must match the entity's owner_id if one is set
        namespace_id: Optional namespace override. Falls back to the configured default.

    Returns:
        JSON string confirming deletion or error message
    """
    resolved_ns = _resolve_namespace(namespace_id)
    logger.info(f"Deleting entity: {entity_id} from namespace: {resolved_ns}")

    try:
        entity = get_client().get_entity_by_id(namespace_id=resolved_ns, entity_id=entity_id)
        if entity is None:
            return json.dumps({"success": False, "error": f"Entity {entity_id} not found"})

        if not _entity_owned_by(entity, user_id, agent_id):
            logger.info(f"Delete denied for entity={entity_id} namespace={resolved_ns}: caller is not owner")
            return json.dumps({"error": "Permission denied: caller is not the owner of this entity"})

        get_client().delete_entity_by_id(namespace_id=resolved_ns, entity_id=entity_id)
        return json.dumps({"success": True, "message": f"Entity {entity_id} deleted successfully"})
    except NamespaceNotFoundException:
        _evict_namespace(resolved_ns)
        return json.dumps({"success": False, "error": f"Namespace '{resolved_ns}' not found"})
    except EvolveException as e:
        logger.exception(f"Error deleting entity {entity_id}: {str(e)}")
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def put_retention_schedule(
    schedule_id: str, definition: dict[str, Any] | str, namespace_id: str, initiated_by: str, expected_revision: int = 0
) -> str:
    """Create or replace a revisioned schedule using a complete definition."""
    try:
        parsed = _retention_object(definition)
    except ValueError as exc:
        return _json_response({"error": str(exc)})
    return _retention_call(
        namespace_id, "put_schedule", schedule_id, parsed, initiated_by=initiated_by, expected_revision=expected_revision
    )


@mcp.tool()
def get_retention_schedule(schedule_id: str, namespace_id: str) -> str:
    """Show stored configuration and upcoming times; suspended schedules have no upcoming runs."""
    return _retention_call(namespace_id, "get_schedule", schedule_id)


@mcp.tool()
def list_retention_schedules(namespace_id: str) -> str:
    """List schedules in the supplied namespace."""
    return _retention_call(namespace_id, "list_schedules")


@mcp.tool()
def delete_retention_schedule(schedule_id: str, namespace_id: str, expected_revision: int) -> str:
    """Delete an inactive schedule at the last observed revision."""
    return _retention_call(namespace_id, "delete_schedule", schedule_id, expected_revision=expected_revision)


@mcp.tool()
def list_retention_jobs(namespace_id: str, schedule_id: str | None = None, limit: int = 100) -> str:
    """List admitted executions in the namespace."""
    return _retention_call(namespace_id, "list_jobs", schedule_id=schedule_id, limit=limit)


@mcp.tool()
def cancel_retention_job(namespace_id: str, job_id: str) -> str:
    """Request cancellation at the next entity-operation boundary."""
    return _retention_call(namespace_id, "cancel_job", job_id)


@mcp.tool()
def acknowledge_interrupted_retention_job(namespace_id: str, job_id: str, worker_stopped: bool) -> str:
    """Recover an interrupted claim after confirming its owning process stopped."""
    return _retention_call(namespace_id, "recover_job", job_id, worker_stopped=worker_stopped)


@mcp.tool()
def create_retention_policy(policy_id: str, namespace_id: str, name: str | None = None, enabled: bool = True) -> str:
    """Create an empty policy; duplicate IDs fail."""
    return _retention_call(namespace_id, "create_policy", policy_id, name=name, enabled=enabled)


@mcp.tool()
def update_retention_policy(policy_id: str, namespace_id: str, name: str | None = None, enabled: bool | None = None) -> str:
    """Update policy metadata, preserving its rules."""
    return _retention_call(namespace_id, "update_policy", policy_id, name=name, enabled=enabled)


@mcp.tool()
def delete_retention_policy(policy_id: str, namespace_id: str) -> str:
    """Delete a policy only if no schedule or active job references it."""
    return _retention_call(namespace_id, "delete_policy", policy_id)


@mcp.tool()
def add_retention_rule(policy_id: str, name: str, rule: dict[str, Any], namespace_id: str) -> str:
    """Append a named rule to the policy. Rule is a structured object."""
    return _retention_call(namespace_id, "add_rule", policy_id, name, rule)


@mcp.tool()
def update_retention_rule(policy_id: str, name: str, changes: dict[str, Any], namespace_id: str) -> str:
    """Update supplied fields of a named rule without changing its position."""
    return _retention_call(namespace_id, "update_rule", policy_id, name, changes)


@mcp.tool()
def list_retention_rules(policy_id: str, namespace_id: str) -> str:
    """List a policy's rules in execution order."""
    return _retention_call(namespace_id, "list_rules", policy_id)


@mcp.tool()
def remove_retention_rule(policy_id: str, name: str, namespace_id: str) -> str:
    """Remove one named rule from a policy."""
    return _retention_call(namespace_id, "remove_rule", policy_id, name)


@mcp.tool()
def create_retention_schedule(schedule_id: str, definition: dict[str, Any], namespace_id: str, initiated_by: str) -> str:
    """Create a schedule from a structured definition; duplicate IDs fail."""
    return _retention_call(namespace_id, "create_schedule", schedule_id, definition, initiated_by=initiated_by)


@mcp.tool()
def update_retention_schedule(
    schedule_id: str, changes: dict[str, Any], namespace_id: str, initiated_by: str, expected_revision: int
) -> str:
    """Update supplied schedule fields using the last observed revision."""
    return _retention_call(
        namespace_id, "update_schedule", schedule_id, changes, initiated_by=initiated_by, expected_revision=expected_revision
    )


@mcp.tool()
def start_retention_schedule(schedule_id: str, namespace_id: str, initiated_by: str, expected_revision: int) -> str:
    """Enable future admissions without changing timing or scope."""
    return _retention_call(namespace_id, "start_schedule", schedule_id, initiated_by=initiated_by, expected_revision=expected_revision)


@mcp.tool()
def stop_retention_schedule(schedule_id: str, namespace_id: str, initiated_by: str, expected_revision: int) -> str:
    """Suspend future admissions without cancelling existing jobs."""
    return _retention_call(namespace_id, "stop_schedule", schedule_id, initiated_by=initiated_by, expected_revision=expected_revision)


@mcp.tool()
def get_retention_job(job_id: str, namespace_id: str) -> str:
    """Get a namespace-owned scheduled execution."""
    return _retention_call(namespace_id, "get_job", job_id)


@mcp.tool()
def get_retention_run(run_id: str, namespace_id: str, agent_id: str | None = None) -> str:
    """Get a persisted report in the requested namespace/agent scope."""
    return _retention_call(namespace_id, "get_run", run_id, agent_id=agent_id)


@mcp.tool()
def mark_retention(policy_id: str, namespace_id: str, initiated_by: str | None = None) -> str:
    """Durably mark deletion candidates without deleting memories or running hooks."""
    return _retention_call(namespace_id, "mark", policy_id, initiated_by=initiated_by)


@mcp.tool()
def sweep_retention(policy_id: str, namespace_id: str, initiated_by: str | None = None) -> str:
    """Sweep marked candidates, honoring current legal holds in the deletion transaction."""
    return _retention_call(namespace_id, "sweep", policy_id, initiated_by=initiated_by)


@mcp.tool()
def list_retention_candidates(namespace_id: str, agent_id: str | None = None, limit: int = 100) -> str:
    """List durable candidate states without memory contents."""
    return _retention_call(namespace_id, "list_candidates", agent_id=agent_id, limit=limit)


@mcp.tool()
def list_retention_audit(namespace_id: str, agent_id: str | None = None, limit: int = 100) -> str:
    """List committed marking and deletion receipts without memory contents."""
    return _retention_call(namespace_id, "list_audit", agent_id=agent_id, limit=limit)


@mcp.tool()
def record_source_deletion(namespace_id: str, source_id: str, user_id: str, agent_id: str, deleted_at: str) -> str:
    """Trusted hosts report committed source deletions; repeated delivery is safe."""
    return _retention_call(namespace_id, "record_source_deletion", source_id, user_id=user_id, agent_id=agent_id, deleted_at=deleted_at)
