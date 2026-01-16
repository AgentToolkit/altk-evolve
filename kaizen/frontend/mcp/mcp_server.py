"""
Katas MCP Server

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
from kaizen.schema.exceptions import NamespaceNotFoundException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("katas-mcp")

mcp = FastMCP("katas")
client = KaizenClient()


def ensure_namespace():
    try:
        client.get_namespace_details(kaizen_config.namespace_id)
    except NamespaceNotFoundException:
        client.create_namespace(kaizen_config.namespace_id)


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
    results = client.search_entities(
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
def save_trajectory(
    trajectory_data: str, task_id: str | None = None
) -> list[RecordedEntity]:
    """
    Save the full agent trajectory to the Kata DB and generate tips

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
                content=message["content"]
                if isinstance(message["content"], str)
                else str(message["content"]),
                metadata={
                    "task_id": task_id,
                    "message": message,  # store the original message for reference
                },
            )
        )

    client.update_entities(
        namespace_id=kaizen_config.namespace_id,
        entities=entities,
        enable_conflict_resolution=False,
    )
    tips = generate_tips(messages)

    client.update_entities(
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

    return client.search_entities(
        namespace_id=kaizen_config.namespace_id,
        filters={"type": "trajectory", "task_id": task_id},
        limit=1000,
    )
