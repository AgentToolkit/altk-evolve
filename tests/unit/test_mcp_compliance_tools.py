import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

from altk_evolve.frontend.mcp.mcp_server import (
    get_compliance_status,
    get_entity,
    list_entities,
    patch_entity_metadata,
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
        },
    )
    client.scan_entities.return_value = [entity]
    result = json.loads(
        run_retention(
            policy=json.dumps(
                {
                    "rules": [
                        {
                            "name": "old-sessions",
                            "entity_type": "trajectory",
                            "max_age_days": 365,
                            "action": "delete",
                            "cascade_derived": True,
                        }
                    ]
                }
            ),
            dry_run=False,
            as_of=NOW.isoformat(),
            run_id="run-1",
            namespace_id="tenant-a",
            metadata_filters=json.dumps({"agent_id": "agent-a"}),
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
