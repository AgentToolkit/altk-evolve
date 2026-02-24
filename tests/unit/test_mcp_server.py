import json
import uuid
import pytest
from unittest.mock import patch, MagicMock

from kaizen.frontend.mcp.mcp_server import save_trajectory, create_entity
from kaizen.schema.conflict_resolution import EntityUpdate


@pytest.fixture
def mock_get_client():
    with patch("kaizen.frontend.mcp.mcp_server.get_client") as mock:
        client_instance = mock.return_value
        yield client_instance


def test_save_trajectory_metadata_injection(mock_get_client):
    # Mock tip generation to prevent actual LLM calls
    with patch("kaizen.frontend.mcp.mcp_server.generate_tips") as mock_generate_tips:
        mock_result = MagicMock()
        mock_tip = MagicMock()
        mock_tip.content = "Always write unit tests"
        mock_tip.category = "testing"
        mock_tip.rationale = "Helps catch bugs early"
        mock_tip.trigger = "writing code"
        mock_result.tips = [mock_tip]
        mock_result.task_description = "Add feature"
        mock_generate_tips.return_value = mock_result

        trajectory_data = json.dumps([{"role": "user", "content": "hi"}])
        task_id = str(uuid.uuid4())

        save_trajectory.fn(trajectory_data=trajectory_data, task_id=task_id)

        # Ensure update_entities was called twice (once for trajectory, once for tips)
        assert mock_get_client.update_entities.call_count == 2

        # Second call is for tips
        call_args = mock_get_client.update_entities.call_args_list[1][1]
        entities = call_args["entities"]

        assert len(entities) == 1
        tip_entity = entities[0]
        assert tip_entity.type == "guideline"
        assert tip_entity.metadata["source_task_id"] == task_id
        assert tip_entity.metadata["creation_mode"] == "auto-mcp"


def test_create_entity_metadata_injection_manual_guideline(mock_get_client):
    mock_update = EntityUpdate(id="123", type="guideline", content="docstrings", event="ADD", metadata={"creation_mode": "manual"})
    mock_get_client.update_entities.return_value = [mock_update]

    # Missing explicit metadata, should auto-inject "manual"
    result_str = create_entity.fn(content="Write clear docstrings", entity_type="guideline")
    result = json.loads(result_str)
    assert result["event"] == "ADD"
    assert "id" in result

    call_args = mock_get_client.update_entities.call_args[1]
    entities = call_args["entities"]
    assert len(entities) == 1
    entity = entities[0]

    assert entity.type == "guideline"
    assert entity.metadata["creation_mode"] == "manual"


def test_create_entity_metadata_injection_manual_policy(mock_get_client):
    mock_update = EntityUpdate(id="123", type="policy", content="PR reviews", event="ADD", metadata={"creation_mode": "manual"})
    mock_get_client.update_entities.return_value = [mock_update]

    result_str = create_entity.fn(content="Require PR reviews", entity_type="policy")
    result = json.loads(result_str)
    assert result["event"] == "ADD"

    call_args = mock_get_client.update_entities.call_args[1]
    entities = call_args["entities"]
    entity = entities[0]

    assert entity.type == "policy"
    assert entity.metadata["creation_mode"] == "manual"


def test_create_entity_no_metadata_injection_for_other_types(mock_get_client):
    mock_update = EntityUpdate(id="123", type="log", content="App started", event="ADD", metadata={})
    mock_get_client.update_entities.return_value = [mock_update]

    # A generic log entity shouldn't get creation_mode injected
    result_str = create_entity.fn(content="App started", entity_type="log")
    result = json.loads(result_str)
    assert result["event"] == "ADD"

    call_args = mock_get_client.update_entities.call_args[1]
    entities = call_args["entities"]
    entity = entities[0]

    assert entity.type == "log"
    assert "creation_mode" not in (entity.metadata or {})
