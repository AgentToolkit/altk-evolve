import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

from altk_evolve.frontend.mcp.mcp_server import (
    delete_entity,
    get_compliance_status,
    get_entity,
    get_retention_policy,
    list_entities,
    list_retention_policies,
    list_retention_runs,
    patch_entity_metadata,
    put_retention_policy,
    record_access,
    run_retention,
    validate_retention_policy,
)
from altk_evolve.schema.core import RecordedEntity

pytestmark = pytest.mark.unit

NOW = datetime.datetime(2026, 7, 24, 12, 0, tzinfo=datetime.UTC)


def _entity(
    entity_id: str,
    *,
    entity_type: str = "fact",
    created_days_ago: int = 0,
    metadata: dict | None = None,
) -> RecordedEntity:
    return RecordedEntity(
        id=entity_id,
        type=entity_type,
        content=f"Memory {entity_id}",
        metadata=metadata or {},
        created_at=NOW - datetime.timedelta(days=created_days_ago),
    )


@pytest.fixture
def client():
    with (
        patch("altk_evolve.frontend.mcp.mcp_server._resolve_namespace", return_value="tenant-a"),
        patch("altk_evolve.frontend.mcp.mcp_server.get_client") as get_client,
    ):
        yield get_client.return_value


def test_list_entities_returns_filtered_paginated_inventory_without_recording_access(client):
    client.scan_entities.return_value = [
        _entity("old", created_days_ago=10, metadata={"user_id": "user-1", "agent_id": "agent-a"}),
        _entity("new", created_days_ago=1, metadata={"user_id": "user-1", "agent_id": "agent-a"}),
        _entity("other-user", metadata={"user_id": "user-2", "agent_id": "agent-a"}),
    ]

    first = json.loads(
        list_entities(
            entity_types=["fact"],
            user_id="user-1",
            agent_id="agent-a",
            limit=1,
            namespace_id="tenant-a",
        )
    )
    second = json.loads(
        list_entities(
            entity_types=["fact"],
            user_id="user-1",
            agent_id="agent-a",
            cursor=first["next_cursor"],
            limit=1,
            namespace_id="tenant-a",
        )
    )

    assert first["total"] == 2
    assert first["items"][0]["id"] == "new"
    assert "content" not in first["items"][0]
    assert second["items"][0]["id"] == "old"
    assert second["next_cursor"] is None
    client.scan_entities.assert_called_with("tenant-a", limit=100_000)
    client.get_all_entities.assert_not_called()


def test_list_entities_can_record_user_facing_access(client):
    entity = _entity("one", metadata={"user_id": "user-1"})
    client.scan_entities.return_value = [entity]
    client.get_entity_by_id.return_value = entity
    client.record_access.return_value = ["one"]

    result = json.loads(list_entities(user_id="user-1", record_access=True, namespace_id="tenant-a"))

    assert result["items"][0]["id"] == "one"
    assert result["items"][0]["metadata"]["last_accessed"]
    client.scan_entities.assert_called_once_with("tenant-a", limit=100_000)
    client.get_entity_by_id.assert_called_once_with("tenant-a", "one")
    client.record_access.assert_called_once()
    assert client.record_access.call_args.args == ("tenant-a", ["one"])
    assert client.record_access.call_args.kwargs["when"].tzinfo is datetime.UTC


def test_list_entities_cursor_counts_scanned_rows_when_access_read_disappears(client):
    entities = [
        _entity("new", created_days_ago=1),
        _entity("old", created_days_ago=2),
    ]
    client.scan_entities.return_value = entities
    client.get_entity_by_id.side_effect = [None, entities[1]]
    client.record_access.return_value = ["old"]

    result = json.loads(list_entities(limit=1, record_access=True, namespace_id="tenant-a"))

    assert result["items"] == []
    assert result["next_cursor"] is not None
    assert json.loads(list_entities(cursor=result["next_cursor"], limit=1, namespace_id="tenant-a"))["items"][0]["id"] == "old"


@pytest.mark.parametrize("metadata", [{"owner_id": "user-1"}, {}])
def test_get_entity_enforces_attributed_owner(client, metadata):
    client.scan_entities.return_value = [_entity("one", metadata=metadata)]

    denied = json.loads(
        get_entity(
            "one",
            user_id="user-2",
            record_access=False,
            namespace_id="tenant-a",
        )
    )

    assert denied["error"].startswith("Permission denied")


def test_get_entity_denies_non_owner_before_access_stamping(client):
    client.scan_entities.return_value = [_entity("one", metadata={"owner_id": "user-1"})]

    denied = json.loads(get_entity("one", user_id="user-2", namespace_id="tenant-a"))

    assert denied["error"].startswith("Permission denied")
    client.get_entity_by_id.assert_not_called()
    client.record_access.assert_not_called()


def test_get_entity_denies_attributed_entity_without_caller_identity(client):
    client.scan_entities.return_value = [_entity("one", metadata={"owner_id": "user-1"})]

    denied = json.loads(get_entity("one", namespace_id="tenant-a"))

    assert denied["error"].startswith("Permission denied")
    client.get_entity_by_id.assert_not_called()
    client.record_access.assert_not_called()


def test_get_entity_enforces_agent_scope(client):
    client.scan_entities.return_value = [
        _entity(
            "one",
            metadata={"owner_id": "user-1", "agent_id": "agent-a"},
        )
    ]

    denied = json.loads(
        get_entity(
            "one",
            user_id="user-1",
            agent_id="agent-b",
            record_access=False,
            namespace_id="tenant-a",
        )
    )

    assert denied["error"].startswith("Permission denied")


def test_patch_entity_metadata_routes_through_client_hook_seam(client):
    original = _entity("one", metadata={"owner_id": "user-1"})
    updated = original.model_copy(update={"metadata": {"owner_id": "user-1", "legal_hold": True}})
    client.scan_entities.return_value = [original]
    client.patch_entity_metadata.return_value = updated

    result = json.loads(
        patch_entity_metadata(
            "one",
            json.dumps({"legal_hold": True}),
            user_id="user-1",
            namespace_id="tenant-a",
        )
    )

    assert result["metadata"]["legal_hold"] is True
    client.patch_entity_metadata.assert_called_once_with("tenant-a", "one", {"legal_hold": True})


def test_patch_entity_metadata_enforces_agent_scope(client):
    client.scan_entities.return_value = [
        _entity(
            "one",
            metadata={"owner_id": "user-1", "agent_id": "agent-a"},
        )
    ]

    denied = json.loads(
        patch_entity_metadata(
            "one",
            json.dumps({"title": "Changed"}),
            user_id="user-1",
            agent_id="agent-b",
            namespace_id="tenant-a",
        )
    )

    assert denied["error"].startswith("Permission denied")
    client.patch_entity_metadata.assert_not_called()


def test_record_access_reports_updated_denied_and_missing_ids(client):
    owned = _entity("owned", metadata={"user_id": "user-1"})
    denied = _entity("denied", metadata={"user_id": "user-2"})
    client.scan_entities.side_effect = [[owned], [denied], []]
    client.record_access.return_value = ["owned"]

    result = json.loads(
        record_access(
            ["owned", "denied", "missing"],
            accessed_at=NOW.isoformat(),
            user_id="user-1",
            namespace_id="tenant-a",
        )
    )

    assert result["updated_ids"] == ["owned"]
    assert result["denied_ids"] == ["denied"]
    assert result["missing_ids"] == ["missing"]
    client.record_access.assert_called_once_with("tenant-a", ["owned"], when=NOW)


def test_record_access_skips_backend_write_when_every_id_is_denied_or_missing(client):
    denied = _entity("denied", metadata={"user_id": "user-2"})
    client.scan_entities.side_effect = [[denied], []]

    result = json.loads(record_access(["denied", "missing"], user_id="user-1", namespace_id="tenant-a"))

    assert result["updated_ids"] == []
    client.record_access.assert_not_called()


def test_record_access_enforces_agent_scope(client):
    other_agent = _entity(
        "other-agent",
        metadata={"user_id": "user-1", "agent_id": "agent-a"},
    )
    client.scan_entities.return_value = [other_agent]

    result = json.loads(
        record_access(
            ["other-agent"],
            user_id="user-1",
            agent_id="agent-b",
            namespace_id="tenant-a",
        )
    )

    assert result["denied_ids"] == ["other-agent"]
    client.record_access.assert_not_called()


def test_delete_entity_enforces_user_and_agent_scope(client):
    entity = _entity(
        "one",
        metadata={"owner_id": "user-1", "agent_id": "agent-a"},
    )
    client.get_entity_by_id.return_value = entity

    denied = json.loads(
        delete_entity(
            "one",
            user_id="user-1",
            agent_id="agent-b",
            namespace_id="tenant-a",
        )
    )

    assert denied["error"].startswith("Permission denied")
    client.delete_entity_by_id.assert_not_called()


def test_delete_entity_denies_attributed_entity_without_caller_identity(client):
    client.get_entity_by_id.return_value = _entity("one", metadata={"owner_id": "user-1"})

    denied = json.loads(delete_entity("one", namespace_id="tenant-a"))

    assert denied["error"].startswith("Permission denied")
    client.delete_entity_by_id.assert_not_called()


def test_validate_retention_policy_normalizes_valid_policy():
    result = json.loads(
        validate_retention_policy(
            json.dumps(
                {
                    "rules": [
                        {
                            "name": "stale-facts",
                            "entity_type": "fact",
                            "max_age_days": 90,
                            "action": "flag",
                        }
                    ]
                }
            )
        )
    )

    assert result["valid"] is True
    assert result["normalized_policy"]["rules"][0]["on_missing_access_signal"] == "skip"


def test_validate_retention_policy_returns_field_errors():
    result = json.loads(validate_retention_policy(json.dumps({"rules": [{"name": "invalid"}]})))

    assert result["valid"] is False
    assert result["errors"]


def test_retention_policy_tools_persist_and_list_namespace_records(client):
    store = MagicMock()
    policy_record = {
        "namespace_id": "tenant-a",
        "policy_id": "standard",
        "name": "Standard retention",
        "description": None,
        "enabled": True,
        "policy": {"rules": []},
    }
    store.put_policy.return_value = policy_record
    store.get_policy.return_value = policy_record
    store.list_policies.return_value = [policy_record]

    with patch("altk_evolve.frontend.mcp.mcp_server._retention_store", return_value=store):
        created = json.loads(
            put_retention_policy(
                policy_id="standard",
                name="Standard retention",
                policy=json.dumps({"rules": []}),
                namespace_id="tenant-a",
            )
        )
        fetched = json.loads(get_retention_policy("standard", namespace_id="tenant-a"))
        listed = json.loads(list_retention_policies(namespace_id="tenant-a"))

    assert created["policy_id"] == "standard"
    assert fetched["name"] == "Standard retention"
    assert listed["items"] == [policy_record]
    store.put_policy.assert_called_once_with(
        namespace_id="tenant-a",
        policy_id="standard",
        name="Standard retention",
        description=None,
        enabled=True,
        policy={"rules": []},
    )


def test_run_retention_returns_real_entity_references_and_predelete_snapshot(client):
    entity = _entity(
        "old-session",
        entity_type="trajectory",
        created_days_ago=400,
        metadata={
            "user_id": "user-1",
            "agent_id": "agent-a",
            "session_id": "thread-9",
            "task_id": "trace-9",
            "title": "Quarterly planning session",
        },
    )
    client.scan_entities.return_value = [entity]
    store = MagicMock()
    store.get_policy.return_value = {
        "policy_id": "standard",
        "name": "Standard retention",
        "enabled": True,
        "policy": {
            "rules": [
                {
                    "name": "old-sessions",
                    "entity_type": "trajectory",
                    "max_age_days": 365,
                    "action": "delete",
                    "cascade_derived": True,
                }
            ]
        },
    }
    with patch("altk_evolve.frontend.mcp.mcp_server._retention_store", return_value=store):
        result = json.loads(
            run_retention(
                policy_id="standard",
                dry_run=False,
                as_of=NOW.isoformat(),
                run_id="run-1",
                namespace_id="tenant-a",
                metadata_filters=json.dumps({"agent_id": "agent-a"}),
                actor_id="operator-a",
            )
        )

    deleted = result["deleted"][0]
    assert result["run_id"] == "run-1"
    assert result["metadata_filters"] == {"agent_id": "agent-a"}
    client.scan_entities.assert_called_once_with(
        "tenant-a",
        filters={"metadata.agent_id": "agent-a"},
        limit=100_000,
    )
    assert deleted["entity_id"] == "old-session"
    assert deleted["outcome"] == "deleted"
    assert deleted["session_id"] == "thread-9"
    assert deleted["content_preview"] == "Memory old-session"
    assert store.save_run.call_count == 2
    assert store.save_run.call_args.kwargs["status"] == "completed"
    assert store.save_run.call_args.kwargs["actor_id"] == "operator-a"
    persisted = store.save_run.call_args.kwargs["report"]
    assert "title" not in persisted["deleted"][0]
    assert "content_preview" not in persisted["deleted"][0]
    assert "metadata" not in persisted["deleted"][0]
    assert "session_id" not in persisted["deleted"][0]


def test_run_retention_applies_external_matches_in_scope(client):
    entity = _entity("orphan", metadata={"agent_id": "agent-a"})
    client.scan_entities.side_effect = [[], [entity]]
    store = MagicMock()
    store.get_policy.return_value = {
        "policy_id": "standard",
        "name": "Standard retention",
        "enabled": True,
        "policy": {"rules": []},
    }

    with patch("altk_evolve.frontend.mcp.mcp_server._retention_store", return_value=store):
        result = json.loads(
            run_retention(
                policy_id="standard",
                dry_run=False,
                namespace_id="tenant-a",
                metadata_filters=json.dumps({"agent_id": "agent-a"}),
                additional_matches=json.dumps(
                    [
                        {
                            "entity_id": "orphan",
                            "rule": "orphaned-conversations",
                            "reason": "orphaned_conversation",
                            "detail": "source conversation is unavailable",
                        }
                    ]
                ),
            )
        )

    assert result["deleted"][0]["entity_id"] == "orphan"
    assert result["deleted"][0]["rule"] == "orphaned-conversations"
    client.delete_entity_by_id.assert_called_once_with("tenant-a", "orphan")
    client.scan_entities.assert_any_call(
        "tenant-a",
        filters={"id": "orphan", "metadata.agent_id": "agent-a"},
        limit=1,
    )


def test_run_retention_persists_failed_status_when_execution_raises(client):
    client.scan_entities.side_effect = RuntimeError("database unavailable")
    store = MagicMock()
    store.get_policy.return_value = {
        "policy_id": "standard",
        "name": "Standard retention",
        "enabled": True,
        "policy": {"rules": []},
    }

    with patch("altk_evolve.frontend.mcp.mcp_server._retention_store", return_value=store):
        result = json.loads(
            run_retention(
                policy_id="standard",
                run_id="run-failed",
                namespace_id="tenant-a",
            )
        )

    assert result["error"] == "Retention run failed"
    assert result["run_id"] == "run-failed"
    assert store.save_run.call_count == 2
    failed_call = store.save_run.call_args
    assert failed_call.kwargs["status"] == "failed"
    assert failed_call.kwargs["report"]["failure"] == {"type": "RuntimeError"}
    assert failed_call.kwargs["report"]["error_count"] == 1


def test_list_retention_runs_filters_by_agent_and_policy(client):
    store = MagicMock()
    store.list_runs.return_value = [{"run_id": "run-1"}]

    with patch("altk_evolve.frontend.mcp.mcp_server._retention_store", return_value=store):
        result = json.loads(
            list_retention_runs(
                namespace_id="tenant-a",
                agent_id="agent-a",
                policy_id="standard",
                limit=20,
            )
        )

    assert result == {"items": [{"run_id": "run-1"}]}
    store.list_runs.assert_called_once_with(
        namespace_id="tenant-a",
        agent_id="agent-a",
        policy_id="standard",
        limit=20,
    )


def test_get_compliance_status_reports_configured_plugin_health(client):
    client.ready.return_value = True
    specs = [
        {
            "name": "access-stamp",
            "kind": "altk_evolve.hooks.plugins.access_stamp.AccessStampPlugin",
            "hooks": ["memory_post_read"],
            "mode": "fire_and_forget",
        }
    ]

    with (
        patch("altk_evolve.frontend.mcp.mcp_server._configured_hook_plugins", return_value=specs),
        patch("altk_evolve.hooks.manager.get_plugin_manager", return_value=MagicMock()),
        patch("altk_evolve.hooks.manager.hooks_active", return_value=True),
        patch("altk_evolve.hooks.types.engine_available", return_value=True),
        patch("altk_evolve.frontend.mcp.mcp_server.version", return_value="1.1.5"),
    ):
        result = json.loads(get_compliance_status(namespace_id="tenant-a"))

    assert result["healthy"] is True
    assert result["retention_available"] is True
    assert result["plugins"][0]["protection_class"] == "access"
    assert result["plugins"][0]["healthy"] is True


def test_get_compliance_status_classifies_redaction_plugins_as_pii(client):
    client.ready.return_value = True
    manager = MagicMock()
    manager.has_hooks_for.return_value = True
    specs = [
        {
            "name": "content-redaction",
            "kind": "example.ContentRedactionPlugin",
            "hooks": ["memory_pre_store"],
            "mode": "sequential",
        }
    ]

    with (
        patch("altk_evolve.frontend.mcp.mcp_server._configured_hook_plugins", return_value=specs),
        patch("altk_evolve.hooks.manager.get_plugin_manager", return_value=manager),
        patch("altk_evolve.hooks.manager.hooks_active", return_value=True),
        patch("altk_evolve.hooks.types.engine_available", return_value=True),
        patch("altk_evolve.frontend.mcp.mcp_server.version", return_value="1.1.5"),
    ):
        result = json.loads(get_compliance_status(namespace_id="tenant-a"))

    assert result["plugins"][0]["protection_class"] == "pii"


def test_get_compliance_status_marks_unregistered_plugin_unhealthy(client):
    client.ready.return_value = True
    manager = MagicMock()
    manager.has_hooks_for.return_value = False
    specs = [
        {
            "name": "legal-hold",
            "kind": "example.LegalHoldPlugin",
            "hooks": ["memory_pre_delete"],
            "mode": "sequential",
        }
    ]

    with (
        patch("altk_evolve.frontend.mcp.mcp_server._configured_hook_plugins", return_value=specs),
        patch("altk_evolve.hooks.manager.get_plugin_manager", return_value=manager),
        patch("altk_evolve.hooks.manager.hooks_active", return_value=False),
        patch("altk_evolve.hooks.types.engine_available", return_value=True),
        patch("altk_evolve.frontend.mcp.mcp_server.version", return_value="1.1.5"),
    ):
        result = json.loads(get_compliance_status(namespace_id="tenant-a"))

    assert result["healthy"] is False
    assert result["plugins"][0]["healthy"] is False


def test_get_compliance_status_handles_non_mapping_hooks_config(client, tmp_path):
    config_path = tmp_path / "hooks.yaml"
    config_path.write_text("- malformed\n", encoding="utf-8")

    with patch("altk_evolve.frontend.mcp.mcp_server.evolve_config.hooks.plugins_yaml", str(config_path)):
        result = json.loads(get_compliance_status(namespace_id="tenant-a"))

    assert result["healthy"] is False
    assert "Unable to read hook configuration" in result["error"]
