"""
Kaizen MCP Server

This server provides a tool to get task-relevant guidelines.
"""

import json
import logging
import uuid

from fastmcp import FastMCP
from kaizen.config.kaizen import kaizen_config
from kaizen.frontend.client.kaizen_client import KaizenClient
from kaizen.llm.tips.tips import generate_tips
from kaizen.schema.core import Entity, RecordedEntity
from kaizen.schema.exceptions import KaizenException, NamespaceNotFoundException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("entities-mcp")

mcp = FastMCP("entities")
_client = None


def get_client() -> KaizenClient:
    """Get or create the KaizenClient singleton.

    This lazy initialization allows tests to configure settings
    before the client is created.
    """
    global _client
    if _client is None:
        _client = KaizenClient()
    return _client


def ensure_namespace():
    try:
        get_client().get_namespace_details(kaizen_config.namespace_id)
    except NamespaceNotFoundException:
        get_client().create_namespace(kaizen_config.namespace_id)


@mcp.tool()
def get_guidelines(task: str) -> str:
    """
    Get relevant guidelines for a given task.
    Provide a task description and receive applicable best practices and guidelines.

    Args:
        task: A description of the task you want guidelines for
    """
    logger.info(f"Getting guidelines for task: {task}")
    ensure_namespace()
    # Get relevant guidelines
    results = get_client().search_entities(
        namespace_id=kaizen_config.namespace_id,
        query=task,
        filters={"type": "guideline"},
    )

    # Format the response
    response_lines = [f"# Guidelines for: {task}\n"]

    for i, guideline in enumerate(results, 1):
        response_lines.append(f"{i}. {guideline.content}")

    return "\n".join(response_lines)


@mcp.tool()
def save_trajectory(trajectory_data: str, task_id: str | None = None) -> list[RecordedEntity]:
    """
    Save the full agent trajectory to the Entity DB and generate tips

    Args:
        trajectory_data: A JSON formatted OpenAI conversation.
        task_id: Optional identifier for the task.
    """
    ensure_namespace()
    task_id = task_id or str(uuid.uuid4())
    entities = []
    messages = json.loads(trajectory_data)
    for message in messages:
        entities.append(
            Entity(
                type="trajectory",
                content=message["content"] if isinstance(message["content"], str) else str(message["content"]),
                metadata={
                    "task_id": task_id,
                    "message": message,  # store the original message for reference
                },
            )
        )

    get_client().update_entities(
        namespace_id=kaizen_config.namespace_id,
        entities=entities,
        enable_conflict_resolution=False,
    )
    tips = generate_tips(messages)

    get_client().update_entities(
        namespace_id=kaizen_config.namespace_id,
        entities=[
            Entity(
                type="guideline",
                content=tip.content,
                metadata={
                    "category": tip.category,
                    "rationale": tip.rationale,
                    "trigger": tip.trigger,
                },
            )
            for tip in tips
        ],
        enable_conflict_resolution=True,
    )

    return get_client().search_entities(
        namespace_id=kaizen_config.namespace_id,
        filters={"type": "trajectory", "task_id": task_id},
        limit=1000,
    )


@mcp.tool()
def create_entity(content: str, entity_type: str, metadata: str | None = None, enable_conflict_resolution: bool = False) -> str:
    """
    Create a single entity in the namespace.

    Args:
        content: The searchable text or structured data for the entity
        entity_type: The type/category of the entity (e.g., 'guideline', 'note', 'fact')
        metadata: Optional JSON string containing arbitrary metadata related to the entity
        enable_conflict_resolution: If True, uses LLM to check for conflicts with existing entities

    Returns:
        JSON string with the entity update details (ADD/UPDATE/DELETE/NONE) and entity ID
    """
    logger.info(f"Creating entity of type: {entity_type}")
    ensure_namespace()

    # Parse metadata if provided
    metadata_dict = None
    if metadata:
        try:
            metadata_dict = json.loads(metadata)
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in metadata parameter: {str(e)}")
            return json.dumps(
                {"error": "Invalid metadata JSON", "message": f"Failed to parse metadata: {str(e)}", "invalid_metadata": metadata}
            )
    else:
        metadata_dict = {}

    # Create the entity using the Entity schema
    entity = Entity(type=entity_type, content=content, metadata=metadata_dict)

    # Use KaizenClient.update_entities() to create the entity
    updates = get_client().update_entities(
        namespace_id=kaizen_config.namespace_id, entities=[entity], enable_conflict_resolution=enable_conflict_resolution
    )

    # Return the first (and only) update result
    if updates:
        update = updates[0]
        return json.dumps(
            {"event": update.event, "id": update.id, "type": update.type, "content": update.content, "metadata": update.metadata}
        )
    else:
        return json.dumps({"error": "Entity creation failed"})


@mcp.tool()
def delete_entity(entity_id: str) -> str:
    """
    Delete a specific entity by its ID.

    Args:
        entity_id: The unique identifier of the entity to delete

    Returns:
        JSON string confirming deletion or error message
    """
    logger.info(f"Deleting entity: {entity_id}")
    ensure_namespace()

    try:
        # Use KaizenClient.delete_entity_by_id() to delete the entity
        get_client().delete_entity_by_id(namespace_id=kaizen_config.namespace_id, entity_id=entity_id)
        return json.dumps({"success": True, "message": f"Entity {entity_id} deleted successfully"})
    except KaizenException as e:
        logger.exception(f"Error deleting entity {entity_id}: {str(e)}")
        return json.dumps({"success": False, "error": str(e)})
