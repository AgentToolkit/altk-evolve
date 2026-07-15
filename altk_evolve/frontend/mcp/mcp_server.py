"""
Evolve MCP Server

This server provides a tool to get task-relevant guidelines.
"""

import json
import logging
import threading
import uuid
import os
from typing import Any

from fastmcp import FastMCP
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from starlette.requests import Request
from starlette.exceptions import HTTPException
from altk_evolve.config.evolve import evolve_config
from altk_evolve.frontend.client.evolve_client import EvolveClient
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


def _empty_store_user_facts_response(user_id: str) -> str:
    return json.dumps({"user_id": user_id, "stored_count": 0, "updates": []})


@mcp.tool()
def store_user_facts(
    user_id: str,
    message: str,
    metadata: str | None = None,
    enable_conflict_resolution: bool = False,
) -> str:
    """Extract and store user facts/preferences for a durable user identity."""
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
        namespace_id=None,
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
) -> list[RecordedEntity]:
    """Fetch fact entities for a user with the legacy fallback chain.

    Order: (1) user filter + query, (2) user filter without query, (3) default
    user with query, (4) default user without query. The default-user fallback
    is skipped when the caller is already ``"default"``.
    """
    client = get_client()
    facts = client.search_entities(
        namespace_id=namespace_id,
        query=query,
        filters={"type": "fact", "metadata.user_id": user_id},
        limit=limit,
    )
    if query and not facts:
        facts = client.search_entities(
            namespace_id=namespace_id,
            query=None,
            filters={"type": "fact", "metadata.user_id": user_id},
            limit=limit,
        )
    if not facts and user_id != "default":
        facts = client.search_entities(
            namespace_id=namespace_id,
            query=query,
            filters={"type": "fact", "metadata.user_id": "default"},
            limit=limit,
        )
        if query and not facts:
            facts = client.search_entities(
                namespace_id=namespace_id,
                query=None,
                filters={"type": "fact", "metadata.user_id": "default"},
                limit=limit,
            )
    return facts


@mcp.tool()
def retrieve_user_facts(user_id: str, query: str | None = None, limit: int = 5) -> str:
    """Retrieve categorized user facts/preferences for a durable user identity."""
    namespace_id = evolve_config.namespace_id

    if limit <= 0 or not get_client().namespace_exists(namespace_id):
        return json.dumps(
            {
                "user_id": user_id,
                "query": query,
                "matched_count": 0,
                "categories": {},
            }
        )

    facts = _search_facts_with_fallback(namespace_id, user_id, query, limit)
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
    if effective_user_id:
        guideline_metadata_base["owner_id"] = effective_user_id
        guideline_metadata_base["user_id"] = effective_user_id
    if session_id:
        guideline_metadata_base["session_id"] = session_id

    # Build entity lists per pipeline so each carries its own generation_method tag,
    # then merge before the single update_entities call.
    guideline_entities = []

    if guidelines_mode in ("regular", "both"):
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
                        "generation_method": "regular",
                        "support": 1,
                    },
                )
                for result in regular_results
                for guideline in result.guidelines
            ]
        except Exception:
            logger.error(
                f"Regular guideline generation failed for task {task_id}, skipping",
                exc_info=True,
            )

    if guidelines_mode in ("consistency", "both"):
        try:
            from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines

            trajectory = {
                "messages": messages,
                "trace_id": task_id,
                "tools": json.loads(tools) if tools else None,
            }
            consistency_results = generate_consistency_guidelines(trajectory)
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
                        "generation_method": "consistency",
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

    Returns:
        JSON string with the entity update details (ADD/UPDATE/DELETE/NONE) and entity ID
    """
    logger.info(f"Creating entity of type: {entity_type} (namespace override: {namespace_id})")
    try:
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

        updates, _ = _persist_entities(
            namespace_id=namespace_id,
            entities=[entity],
            enable_conflict_resolution=enable_conflict_resolution,
        )

        if updates:
            update = updates[0]
            return json.dumps(
                {"event": update.event, "id": update.id, "type": update.type, "content": update.content, "metadata": update.metadata}
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
def delete_entity(entity_id: str, user_id: str | None = None, namespace_id: str | None = None) -> str:
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

        existing_owner = (entity.metadata or {}).get("owner_id")
        if existing_owner is not None and user_id != existing_owner:
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
