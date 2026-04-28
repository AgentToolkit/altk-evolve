import datetime
import json
import uuid
import pytest
from unittest.mock import patch, MagicMock

import altk_evolve.frontend.mcp.mcp_server as mcp_server_module
from altk_evolve.frontend.mcp.mcp_server import save_trajectory, create_entity
from altk_evolve.schema.core import Namespace
from altk_evolve.schema.conflict_resolution import EntityUpdate

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_get_client():
    with patch("altk_evolve.frontend.mcp.mcp_server.get_client") as mock:
        client_instance = mock.return_value
        yield client_instance


def test_save_trajectory_metadata_injection(mock_get_client):
    # Mock guideline generation to prevent actual LLM calls
    with patch("altk_evolve.frontend.mcp.mcp_server.generate_guidelines") as mock_generate_guidelines:
        mock_result = MagicMock()
        mock_guideline = MagicMock()
        mock_guideline.content = "Always write unit tests"
        mock_guideline.category = "testing"
        mock_guideline.rationale = "Helps catch bugs early"
        mock_guideline.trigger = "writing code"
        mock_result.guidelines = [mock_guideline]
        mock_result.task_description = "Add feature"
        mock_generate_guidelines.return_value = [mock_result]

        trajectory_data = json.dumps([{"role": "user", "content": "hi"}])
        task_id = str(uuid.uuid4())

        save_trajectory(trajectory_data=trajectory_data, task_id=task_id)

        # Ensure update_entities was called twice (once for trajectory, once for guidelines)
        assert mock_get_client.update_entities.call_count == 2

        # Second call is for guidelines
        call_args = mock_get_client.update_entities.call_args_list[1][1]
        entities = call_args["entities"]

        assert len(entities) == 1
        guideline_entity = entities[0]
        assert guideline_entity.type == "guideline"
        assert guideline_entity.metadata["source_task_id"] == task_id
        assert guideline_entity.metadata["creation_mode"] == "auto-mcp"


def test_create_entity_metadata_injection_manual_guideline(mock_get_client):
    mock_update = EntityUpdate(id="123", type="guideline", content="docstrings", event="ADD", metadata={"creation_mode": "manual"})
    mock_get_client.update_entities.return_value = [mock_update]

    # Missing explicit metadata, should auto-inject "manual"
    result_str = create_entity(content="Write clear docstrings", entity_type="guideline")
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

    result_str = create_entity(content="Require PR reviews", entity_type="policy")
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
    result_str = create_entity(content="App started", entity_type="log")
    result = json.loads(result_str)
    assert result["event"] == "ADD"

    call_args = mock_get_client.update_entities.call_args[1]
    entities = call_args["entities"]
    entity = entities[0]

    assert entity.type == "log"
    assert "creation_mode" not in (entity.metadata or {})


def test_get_client_uses_idempotent_namespace_bootstrap(monkeypatch):
    original_client = mcp_server_module._client
    original_namespaces = mcp_server_module._initialized_namespaces.copy()

    fake_client = MagicMock()
    created_namespace = Namespace(id="evolve", created_at=datetime.datetime.now(datetime.UTC))
    fake_client.ensure_namespace.return_value = created_namespace

    try:
        mcp_server_module._client = None
        mcp_server_module._initialized_namespaces.clear()

        monkeypatch.setattr(mcp_server_module, "EvolveClient", lambda: fake_client)

        client = mcp_server_module.get_client()

        assert client is fake_client
        fake_client.ensure_namespace.assert_called_once_with(mcp_server_module.evolve_config.namespace_id)
        fake_client.get_namespace_details.assert_not_called()
        fake_client.create_namespace.assert_not_called()
    finally:
        mcp_server_module._client = original_client
        mcp_server_module._initialized_namespaces.clear()
        mcp_server_module._initialized_namespaces.update(original_namespaces)


# ---------------------------------------------------------------------------
# Multi-user / multi-namespace tests
# ---------------------------------------------------------------------------


def test_resolve_namespace_falls_back_to_default():
    """When namespace_id is None, _resolve_namespace returns the configured default."""
    original_namespaces = mcp_server_module._initialized_namespaces.copy()
    try:
        # Pre-populate so ensure_namespace is not called
        mcp_server_module._initialized_namespaces.add(mcp_server_module.evolve_config.namespace_id)
        resolved = mcp_server_module._resolve_namespace(None)
        assert resolved == mcp_server_module.evolve_config.namespace_id
    finally:
        mcp_server_module._initialized_namespaces.clear()
        mcp_server_module._initialized_namespaces.update(original_namespaces)


def test_resolve_namespace_uses_explicit_namespace(mock_get_client):
    """When a custom namespace_id is provided, it is used and ensure_namespace is called."""
    original_namespaces = mcp_server_module._initialized_namespaces.copy()
    created_namespace = Namespace(id="tenant-42", created_at=datetime.datetime.now(datetime.UTC))
    mock_get_client.ensure_namespace.return_value = created_namespace

    try:
        mcp_server_module._initialized_namespaces.discard("tenant-42")
        resolved = mcp_server_module._resolve_namespace("tenant-42")
        assert resolved == "tenant-42"
        assert "tenant-42" in mcp_server_module._initialized_namespaces
        mock_get_client.ensure_namespace.assert_called_with("tenant-42")
    finally:
        mcp_server_module._initialized_namespaces.clear()
        mcp_server_module._initialized_namespaces.update(original_namespaces)


def test_resolve_namespace_caches_after_first_call(mock_get_client):
    """Second call with the same namespace should NOT call ensure_namespace again."""
    original_namespaces = mcp_server_module._initialized_namespaces.copy()
    created_namespace = Namespace(id="tenant-99", created_at=datetime.datetime.now(datetime.UTC))
    mock_get_client.ensure_namespace.return_value = created_namespace

    try:
        mcp_server_module._initialized_namespaces.discard("tenant-99")
        mcp_server_module._resolve_namespace("tenant-99")
        mcp_server_module._resolve_namespace("tenant-99")
        # ensure_namespace should be called exactly once for this namespace
        ensure_calls = [c for c in mock_get_client.ensure_namespace.call_args_list if c[0] == ("tenant-99",)]
        assert len(ensure_calls) == 1
    finally:
        mcp_server_module._initialized_namespaces.clear()
        mcp_server_module._initialized_namespaces.update(original_namespaces)


def test_save_trajectory_with_user_and_session_metadata(mock_get_client):
    """save_trajectory should inject user_id and session_id into entity metadata."""
    with patch("altk_evolve.frontend.mcp.mcp_server.generate_guidelines") as mock_gen:
        mock_result = MagicMock()
        mock_guideline = MagicMock()
        mock_guideline.content = "Test guideline"
        mock_guideline.category = "testing"
        mock_guideline.rationale = "reason"
        mock_guideline.trigger = "trigger"
        mock_guideline.implementation_steps = "steps"
        mock_result.guidelines = [mock_guideline]
        mock_result.task_description = "desc"
        mock_gen.return_value = [mock_result]

        trajectory_data = json.dumps([{"role": "user", "content": "hello"}])
        save_trajectory(
            trajectory_data=trajectory_data,
            task_id="task-1",
            user_id="user-42",
            session_id="session-7",
        )

        assert mock_get_client.update_entities.call_count == 2

        # Check trajectory entities (first call)
        traj_call = mock_get_client.update_entities.call_args_list[0][1]
        traj_entity = traj_call["entities"][0]
        assert traj_entity.metadata["user_id"] == "user-42"
        assert traj_entity.metadata["session_id"] == "session-7"
        assert traj_entity.metadata["task_id"] == "task-1"

        # Check guideline entities (second call)
        guide_call = mock_get_client.update_entities.call_args_list[1][1]
        guide_entity = guide_call["entities"][0]
        assert guide_entity.metadata["user_id"] == "user-42"
        assert guide_entity.metadata["owner_id"] == "user-42"
        assert guide_entity.metadata["session_id"] == "session-7"


def test_save_trajectory_with_namespace_override(mock_get_client):
    """save_trajectory should use the provided namespace_id instead of default."""
    original_namespaces = mcp_server_module._initialized_namespaces.copy()
    created_namespace = Namespace(id="custom-ns", created_at=datetime.datetime.now(datetime.UTC))
    mock_get_client.ensure_namespace.return_value = created_namespace

    with patch("altk_evolve.frontend.mcp.mcp_server.generate_guidelines") as mock_gen:
        mock_result = MagicMock()
        mock_result.guidelines = []
        mock_gen.return_value = mock_result

        trajectory_data = json.dumps([{"role": "user", "content": "hi"}])

        try:
            mcp_server_module._initialized_namespaces.discard("custom-ns")
            save_trajectory(
                trajectory_data=trajectory_data,
                task_id="task-2",
                namespace_id="custom-ns",
            )

            traj_call = mock_get_client.update_entities.call_args_list[0][1]
            assert traj_call["namespace_id"] == "custom-ns"
        finally:
            mcp_server_module._initialized_namespaces.clear()
            mcp_server_module._initialized_namespaces.update(original_namespaces)


def test_save_trajectory_backward_compat_no_extra_params(mock_get_client):
    """Calling save_trajectory without new params should still work (backward compat)."""
    with patch("altk_evolve.frontend.mcp.mcp_server.generate_guidelines") as mock_gen:
        mock_result = MagicMock()
        mock_result.guidelines = []
        mock_gen.return_value = mock_result

        trajectory_data = json.dumps([{"role": "user", "content": "hi"}])
        save_trajectory(trajectory_data=trajectory_data, task_id="task-3")

        traj_call = mock_get_client.update_entities.call_args_list[0][1]
        traj_entity = traj_call["entities"][0]
        # user_id and session_id should NOT be in metadata when not provided
        assert "user_id" not in traj_entity.metadata
        assert "session_id" not in traj_entity.metadata
