"""
Kaizen MCP Server

This server provides a tool to get task-relevant guidelines.
"""

import json
import logging
import threading
import uuid

from fastmcp import FastMCP
from kaizen.config.kaizen import kaizen_config
from kaizen.frontend.client.kaizen_client import KaizenClient
from kaizen.llm.tips.tips import generate_tips
from kaizen.schema.core import Entity, RecordedEntity
from kaizen.schema.exceptions import KaizenException, NamespaceNotFoundException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("entities-mcp")

_client = None
_namespace_initialized = False
_client_init_lock = threading.Lock()

mcp = FastMCP("entities")


def get_client() -> KaizenClient:
    """Get the KaizenClient singleton with lazy initialization.

    Initializes the client and ensures namespace exists on first access.
    This avoids the FastMCP SSE lifespan initialization race condition.
    """
    global _client, _namespace_initialized

    with _client_init_lock:
        if _client is None:
            logger.info("Initializing Kaizen client...")
            _client = KaizenClient()
            logger.info("Kaizen client initialized")

        if not _namespace_initialized:
            logger.info("Ensuring namespace exists...")
            try:
                _client.get_namespace_details(kaizen_config.namespace_id)
                logger.info(f"Namespace '{kaizen_config.namespace_id}' already exists")
            except NamespaceNotFoundException:
                logger.info(f"Creating namespace '{kaizen_config.namespace_id}'")
                _client.create_namespace(kaizen_config.namespace_id)
                logger.info(f"Namespace '{kaizen_config.namespace_id}' created successfully")
            except Exception as e:
                logger.error(f"Error ensuring namespace: {e}")
                try:
                    _client.create_namespace(kaizen_config.namespace_id)
                    logger.info(f"Namespace '{kaizen_config.namespace_id}' created after error")
                except Exception as create_error:
                    logger.error(f"Failed to create namespace: {create_error}")
                    raise
            _namespace_initialized = True
            logger.info("Namespace initialization complete")

        return _client


@mcp.tool()
def get_guidelines(task: str) -> str:
    """
    Get relevant guidelines for a given task.
    Provide a task description and receive applicable best practices and guidelines.

    Args:
        task: A description of the task you want guidelines for
    """
    logger.info(f"Getting guidelines for task: {task}")
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
    result = generate_tips(messages)

    if result.tips:
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
                        "task_description": result.task_description,
                    },
                )
                for tip in result.tips
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
    try:
        # Parse metadata if provided
        metadata_dict = {}
        if metadata:
            try:
                metadata_dict = json.loads(metadata)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in metadata parameter: {str(e)}")
                return json.dumps(
                    {"error": "Invalid metadata JSON", "message": f"Failed to parse metadata: {str(e)}", "invalid_metadata": metadata}
                )

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

    except Exception as e:
        import traceback

        traceback.print_exc()
        logger.exception(f"CRASH IN CREATE_ENTITY: {e}")
        return json.dumps({"error": f"Server Error: {str(e)}"})


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

    try:
        # Use KaizenClient.delete_entity_by_id() to delete the entity
        get_client().delete_entity_by_id(namespace_id=kaizen_config.namespace_id, entity_id=entity_id)
        return json.dumps({"success": True, "message": f"Entity {entity_id} deleted successfully"})
    except KaizenException as e:
        logger.exception(f"Error deleting entity {entity_id}: {str(e)}")
        return json.dumps({"success": False, "error": str(e)})
