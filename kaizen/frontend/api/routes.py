from typing import Any, List, Optional
import logging
from pydantic import BaseModel

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter()


class NamespaceCreateRequest(BaseModel):
    namespace_id: str


class EntityCreateRequest(BaseModel):
    type: str
    content: str
    metadata: dict = {}


@router.get("/dashboard")
def get_dashboard() -> dict[str, Any]:
    from kaizen.frontend.mcp.mcp_server import get_client

    client = get_client()

    # 1. Backend health
    try:
        health = client.ready()
    except Exception as e:
        logger.error(f"Error checking health: {e}")
        health = False

    # 2. Namespace count
    try:
        namespaces = client.all_namespaces(limit=1000)
        namespace_count = len(namespaces)
    except Exception as e:
        logger.error(f"Error fetching namespaces: {e}")
        namespaces = []
        namespace_count = 0

    # 3. Entity counts and recent entities across namespaces
    # For MVP, we will aggregate from all available namespaces up to the limit
    total_entities = 0
    type_breakdown: dict[str, int] = {}
    recent_entities: list[dict[str, Any]] = []

    for ns in namespaces:
        try:
            total_entities += ns.num_entities or 0
            # Fetch only a small sample per namespace for the dashboard
            ns_entities = client.get_all_entities(ns.id, limit=10)

            for entity in ns_entities:
                etype = entity.type or "unknown"
                type_breakdown[etype] = type_breakdown.get(etype, 0) + 1

                # Safely handle non-string content before slicing
                content = entity.content
                if isinstance(content, str):
                    snippet = content[:100] + "..." if len(content) > 100 else content
                else:
                    safe_str = str(content)
                    snippet = safe_str[:100] + "..." if len(safe_str) > 100 else safe_str

                recent_entities.append(
                    {
                        "id": entity.id,
                        "type": entity.type,
                        "content": snippet,
                        "namespace": ns.id,
                        "created_at": entity.created_at.isoformat() if hasattr(entity, "created_at") and entity.created_at else None,
                    }
                )
        except Exception as e:
            logger.error(f"Error fetching entities for namespace {ns.id}: {e}")

    # sort by created_at descending (assuming we have those or just use the end of list)
    # the client doesn't strictly order by date right now unless we extract or sort manually
    recent_entities.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    recent_entities = recent_entities[:10]  # top 10

    return {
        "health": health,
        "namespace_count": namespace_count,
        "total_entities": total_entities,
        "type_breakdown": [{"type": k, "count": v} for k, v in type_breakdown.items()],
        "recent_entities": recent_entities,
    }


@router.get("/namespaces")
def list_namespaces() -> List[dict[str, Any]]:
    from kaizen.frontend.mcp.mcp_server import get_client

    client = get_client()
    try:
        namespaces = []
        for ns in client.all_namespaces(limit=1000):
            namespaces.append({"id": ns.id, "amount_of_entities": ns.num_entities or 0})
        return namespaces
    except Exception as e:
        logger.error(f"Error fetching namespaces: {e}")
        return []


@router.post("/namespaces")
def add_namespace(req: NamespaceCreateRequest) -> dict[str, Any]:
    from kaizen.frontend.mcp.mcp_server import get_client

    client = get_client()
    try:
        client.create_namespace(req.namespace_id)
        return {"success": True, "namespace_id": req.namespace_id}
    except Exception as e:
        from fastapi import HTTPException

        logger.error(f"Error creating namespace: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/namespaces/{namespace_id}")
def delete_namespace(namespace_id: str) -> dict[str, Any]:
    from kaizen.frontend.mcp.mcp_server import get_client

    client = get_client()
    try:
        client.delete_namespace(namespace_id)
        return {"success": True}
    except Exception as e:
        from fastapi import HTTPException

        logger.error(f"Error deleting namespace: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/namespaces/{namespace_id}/entities")
def list_namespace_entities(
    namespace_id: str,
    type: Optional[str] = Query(None, description="Filter entities by type (e.g., guideline, task)"),
    limit: int = Query(100, description="Maximum number of entities to return"),
) -> List[dict[str, Any]]:
    from kaizen.frontend.mcp.mcp_server import get_client

    client = get_client()
    try:
        filters = {}
        if type:
            filters["type"] = type

        entities = client.get_all_entities(namespace_id, filters=filters, limit=limit)

        result = []
        for entity in entities:
            result.append(
                {
                    "id": entity.id,
                    "type": entity.type,
                    "content": entity.content,
                    "metadata": entity.metadata or {},
                    "created_at": entity.created_at.isoformat() if hasattr(entity, "created_at") and entity.created_at else None,
                }
            )

        # Sort by created_at descending
        result.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return result
    except Exception as e:
        logger.error(f"Error fetching entities for namespace {namespace_id}: {e}")
        return []


@router.delete("/namespaces/{namespace_id}/entities/{entity_id}")
def delete_namespace_entity(namespace_id: str, entity_id: str) -> dict[str, Any]:
    from kaizen.frontend.mcp.mcp_server import get_client

    client = get_client()
    try:
        client.delete_entity_by_id(namespace_id, entity_id)
        return {"success": True}
    except Exception as e:
        from fastapi import HTTPException

        logger.error(f"Error deleting entity {entity_id} from namespace {namespace_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/namespaces/{namespace_id}/entities")
def create_namespace_entity(namespace_id: str, req: EntityCreateRequest) -> dict[str, Any]:
    from kaizen.frontend.mcp.mcp_server import get_client
    from kaizen.schema.core import Entity
    from fastapi import HTTPException

    # 1. Enforce specific schema typing prior to insertion
    if req.type == "guideline":
        from kaizen.schema.tips import Tip

        try:
            # Tip expects content at the root, so we map req.content and unpack the metadata
            tip_meta = {k: v for k, v in req.metadata.items() if k != "content"}
            Tip(content=req.content, **tip_meta)
        except Exception as e:
            logger.error(f"Guideline validation failed: {e}")
            raise HTTPException(status_code=422, detail=f"Invalid guideline metadata schema: {e}")

    elif req.type == "policy":
        try:
            from kaizen.schema.policy import Policy  # type: ignore[import-not-found]

            try:
                # The Policy model checks the full payload
                policy_meta = {k: v for k, v in req.metadata.items() if k not in ("content", "type")}
                Policy(content=req.content, type=req.type, **policy_meta)
            except Exception as e:
                logger.error(f"Policy validation failed: {e}")
                raise HTTPException(status_code=422, detail=f"Invalid policy metadata schema: {e}")
        except ImportError:
            # Fallback if we're on a branch where kaizen.schema.policy doesn't exist yet
            logger.warning("Policy schema missing. Skipping strict validation.")

    client = get_client()
    try:
        new_entity = Entity(type=req.type, content=req.content, metadata=req.metadata)
        # Using enable_conflict_resolution=False for a direct insert
        updates = client.update_entities(namespace_id, [new_entity], enable_conflict_resolution=False)
        if not updates:
            raise Exception("Failed to insert entity. No updates returned.")
        return {"success": True, "id": updates[0].id}
    except Exception as e:
        from fastapi import HTTPException

        logger.error(f"Error creating entity in namespace {namespace_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
