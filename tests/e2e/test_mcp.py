import pytest
import json
from fastmcp.client import Client
from pathlib import Path
from dotenv import load_dotenv

__data__ = Path(__file__).parent.parent / "data"
load_dotenv()


@pytest.mark.e2e
async def test_save_trajectory_and_retrieve_guidelines(mcp):
    async with Client(transport=mcp) as evolve_mcp:
        trajectory = (__data__ / "trajectory.json").read_text()
        response = await evolve_mcp.call_tool_mcp("save_trajectory", {"trajectory_data": trajectory, "task_id": "123"})
        saved_trajectory = json.loads(response.content[0].text)
        # MCP server should return entity versions of the trajectory
        assert len(saved_trajectory) == 19
        response = await evolve_mcp.call_tool_mcp(
            "get_guidelines",
            {"task": "What states do I have teammates in? Read the list from the states.txt file. use the filesystem mcp tool"},
        )
        guidelines = response.content[0].text
        assert "# Guidelines for: " in guidelines

        # Verify guideline provenance in Evolve backend
        from altk_evolve.frontend.client.evolve_client import EvolveClient
        from altk_evolve.config.evolve import evolve_config

        client = EvolveClient()
        entities = client.search_entities(
            namespace_id=evolve_config.namespace_id,
            filters={"type": "guideline"},
            limit=10,
        )
        assert len(entities) > 0
        for entity in entities:
            metadata = entity.metadata or {}
            assert metadata.get("source_task_id") == "123"
            assert metadata.get("creation_mode") == "auto-mcp"


@pytest.mark.e2e
async def test_create_entity_without_conflict_resolution(mcp):
    """Test creating a single entity without conflict resolution."""
    async with Client(transport=mcp) as evolve_mcp:
        response = await evolve_mcp.call_tool_mcp(
            "create_entity",
            {
                "content": "Always use type hints in Python functions",
                "entity_type": "guideline",
                "metadata": json.dumps({"category": "code_quality", "language": "python"}),
                "enable_conflict_resolution": False,
            },
        )

        result = json.loads(response.content[0].text)

        # Verify entity was created (ADD event)
        assert result["event"] == "ADD"
        assert "id" in result
        assert result["type"] == "guideline"
        assert result["content"] == "Always use type hints in Python functions"
        assert result["metadata"]["category"] == "code_quality"


@pytest.mark.e2e
async def test_create_entity_with_conflict_resolution(mcp):
    """Test creating an entity with conflict resolution enabled."""
    from unittest.mock import patch
    from altk_evolve.schema.conflict_resolution import EntityUpdate

    async with Client(transport=mcp) as evolve_mcp:
        # Create first entity
        response = await evolve_mcp.call_tool_mcp(
            "create_entity", {"content": "Use descriptive variable names", "entity_type": "guideline", "enable_conflict_resolution": False}
        )

        assert not response.isError
        result = json.loads(response.content[0].text)
        assert result["event"] == "ADD"
        first_entity_id = result["id"]

        # Mock resolve_conflicts to avoid LLM call timeout
        patch_target = "altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts"

        with patch(patch_target) as mock_resolve:
            # Configure mock to return an UPDATE event
            mock_resolve.return_value = [
                EntityUpdate(
                    id=str(first_entity_id), type="guideline", content="Always use descriptive variable names", event="UPDATE", metadata={}
                )
            ]

            # Create similar entity with conflict resolution
            response = await evolve_mcp.call_tool_mcp(
                "create_entity",
                {"content": "Always use descriptive variable names", "entity_type": "guideline", "enable_conflict_resolution": True},
            )

            assert not response.isError
            result = json.loads(response.content[0].text)

            # Should return what our mock returned
            assert result["event"] == "UPDATE"
            assert result["id"] == str(first_entity_id)


@pytest.mark.e2e
async def test_create_entity_without_metadata(mcp):
    """Test creating an entity without optional metadata."""
    async with Client(transport=mcp) as evolve_mcp:
        response = await evolve_mcp.call_tool_mcp(
            "create_entity", {"content": "Simple entity without metadata", "entity_type": "note", "enable_conflict_resolution": False}
        )

        result = json.loads(response.content[0].text)

        # Verify entity was created
        assert result["event"] == "ADD"
        assert "id" in result
        assert result["type"] == "note"
        assert result["content"] == "Simple entity without metadata"


@pytest.mark.e2e
async def test_delete_entity(mcp):
    """Test deleting an entity via MCP."""
    async with Client(transport=mcp) as evolve_mcp:
        # Create an entity
        create_response = await evolve_mcp.call_tool_mcp(
            "create_entity",
            {
                "content": "Temporary test entity",
                "entity_type": "test",
                "metadata": json.dumps({"temp": True}),
                "enable_conflict_resolution": False,
            },
        )

        created_entity = json.loads(create_response.content[0].text)
        entity_id = created_entity["id"]

        # Delete the entity
        delete_response = await evolve_mcp.call_tool_mcp("delete_entity", {"entity_id": entity_id})

        result = json.loads(delete_response.content[0].text)

        # Verify deletion was successful
        assert result["success"] is True
        assert entity_id in result["message"]


@pytest.mark.e2e
async def test_create_and_delete_workflow(mcp):
    """Test complete workflow: create then delete."""
    async with Client(transport=mcp) as evolve_mcp:
        # Create entity
        create_response = await evolve_mcp.call_tool_mcp(
            "create_entity",
            {
                "content": "Workflow test entity",
                "entity_type": "test",
                "metadata": json.dumps({"workflow": "test"}),
                "enable_conflict_resolution": False,
            },
        )
        created = json.loads(create_response.content[0].text)
        entity_id = created["id"]
        assert created["event"] == "ADD"

        # Delete entity
        delete_response = await evolve_mcp.call_tool_mcp("delete_entity", {"entity_id": entity_id})
        delete_result = json.loads(delete_response.content[0].text)
        assert delete_result["success"] is True


@pytest.mark.e2e
async def test_create_multiple_entities_same_type(mcp):
    """Test creating multiple entities of the same type."""
    async with Client(transport=mcp) as evolve_mcp:
        entity_ids = []

        # Create 3 entities
        for i in range(3):
            response = await evolve_mcp.call_tool_mcp(
                "create_entity", {"content": f"Test guideline number {i}", "entity_type": "guideline", "enable_conflict_resolution": False}
            )
            result = json.loads(response.content[0].text)
            assert result["event"] == "ADD"
            entity_ids.append(result["id"])

        # Verify all have unique IDs
        assert len(set(entity_ids)) == 3

        # Clean up
        for entity_id in entity_ids:
            await evolve_mcp.call_tool_mcp("delete_entity", {"entity_id": entity_id})


@pytest.mark.e2e
async def test_create_entity_with_invalid_json_metadata(mcp):
    """Test creating an entity with invalid JSON metadata."""
    async with Client(transport=mcp) as evolve_mcp:
        response = await evolve_mcp.call_tool_mcp(
            "create_entity",
            {
                "content": "Test entity with bad metadata",
                "entity_type": "test",
                "metadata": "{invalid json here}",
                "enable_conflict_resolution": False,
            },
        )

        result = json.loads(response.content[0].text)

        # Should return an error
        assert "error" in result
        assert result["error"] == "Invalid JSON"
        assert "message" in result
        assert "invalid_metadata" in result
