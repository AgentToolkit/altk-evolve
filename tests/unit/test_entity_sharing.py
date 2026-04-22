"""Tests for Phase 1A + 1B entity sharing: visibility, publish, unpublish, get_public."""

import json
import pytest
from unittest.mock import patch

from altk_evolve.frontend.mcp.mcp_server import create_entity, publish_entity, unpublish_entity, get_entities
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import RecordedEntity
import datetime

pytestmark = pytest.mark.unit

NOW = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)


@pytest.fixture
def mock_get_client():
    with patch("altk_evolve.frontend.mcp.mcp_server.get_client") as mock:
        yield mock.return_value


# ── create_entity ─────────────────────────────────────────────────────────────


def test_create_entity_stores_visibility(mock_get_client):
    mock_get_client.update_entities.return_value = [
        EntityUpdate(id="1", type="guideline", content="test", event="ADD", metadata={"visibility": "private"})
    ]
    create_entity(content="test", entity_type="guideline")
    entities = mock_get_client.update_entities.call_args[1]["entities"]
    assert entities[0].metadata["visibility"] == "private"


def test_create_entity_public_visibility(mock_get_client):
    mock_get_client.update_entities.return_value = [
        EntityUpdate(id="1", type="guideline", content="test", event="ADD", metadata={"visibility": "public", "owner_id": "alice"})
    ]
    create_entity(content="test", entity_type="guideline", visibility="public", owner_id="alice")
    entities = mock_get_client.update_entities.call_args[1]["entities"]
    assert entities[0].metadata["visibility"] == "public"
    assert entities[0].metadata["owner_id"] == "alice"


def test_create_entity_invalid_visibility(mock_get_client):
    result = json.loads(create_entity(content="test", entity_type="guideline", visibility="team"))
    assert "error" in result
    mock_get_client.update_entities.assert_not_called()


def test_create_entity_no_owner_id_omitted(mock_get_client):
    mock_get_client.update_entities.return_value = [EntityUpdate(id="1", type="note", content="x", event="ADD", metadata={})]
    create_entity(content="x", entity_type="note")
    entities = mock_get_client.update_entities.call_args[1]["entities"]
    assert "owner_id" not in entities[0].metadata


# ── publish_entity ─────────────────────────────────────────────────────────────


def _make_entity(entity_id="42", visibility="private", owner_id=None):
    meta = {"visibility": visibility}
    if owner_id:
        meta["owner_id"] = owner_id
    return RecordedEntity(id=entity_id, type="guideline", content="tip content", created_at=NOW, metadata=meta)


def test_publish_entity_sets_public(mock_get_client):
    updated = _make_entity(visibility="public", owner_id="alice")
    mock_get_client.get_entity_by_id.return_value = _make_entity(owner_id="alice")
    mock_get_client.patch_entity_metadata.return_value = updated

    result = json.loads(publish_entity(entity_id="42", user_id="alice"))

    mock_get_client.patch_entity_metadata.assert_called_once()
    call_kwargs = mock_get_client.patch_entity_metadata.call_args[1]
    assert call_kwargs["entity_id"] == "42"
    assert call_kwargs["metadata_updates"]["visibility"] == "public"
    assert call_kwargs["metadata_updates"]["owner_id"] == "alice"
    assert "published_at" in call_kwargs["metadata_updates"]
    assert result["metadata"]["visibility"] == "public"


def test_publish_entity_no_user_id(mock_get_client):
    updated = _make_entity(visibility="public")
    mock_get_client.get_entity_by_id.return_value = _make_entity()
    mock_get_client.patch_entity_metadata.return_value = updated

    publish_entity(entity_id="42")

    call_kwargs = mock_get_client.patch_entity_metadata.call_args[1]
    assert "owner_id" not in call_kwargs["metadata_updates"]


def test_publish_entity_not_found(mock_get_client):
    mock_get_client.get_entity_by_id.return_value = None

    result = json.loads(publish_entity(entity_id="99", user_id="alice"))
    assert "error" in result


# ── unpublish_entity ───────────────────────────────────────────────────────────


def test_unpublish_entity_reverts_to_private(mock_get_client):
    updated = _make_entity(visibility="private")
    mock_get_client.get_entity_by_id.return_value = _make_entity()
    mock_get_client.patch_entity_metadata.return_value = updated

    result = json.loads(unpublish_entity(entity_id="42"))

    call_kwargs = mock_get_client.patch_entity_metadata.call_args[1]
    assert call_kwargs["metadata_updates"]["visibility"] == "private"
    assert call_kwargs["metadata_updates"]["published_at"] is None
    assert result["metadata"]["visibility"] == "private"


# ── get_entities with include_public ──────────────────────────────────────────


def test_get_entities_include_public_annotates_results(mock_get_client):
    private = _make_entity(entity_id="1", visibility="private")
    public = _make_entity(entity_id="2", visibility="public", owner_id="bob")

    mock_get_client.search_entities.return_value = [private]
    mock_get_client.get_public_entities.return_value = [public]

    result = get_entities(task="some task", include_public=True)

    assert "[public: bob]" in result
    mock_get_client.get_public_entities.assert_called_once()


def test_get_entities_no_include_public_skips_cross_namespace(mock_get_client):
    mock_get_client.search_entities.return_value = []

    get_entities(task="some task", include_public=False)

    mock_get_client.get_public_entities.assert_not_called()


def test_get_entities_deduplicates_public_already_in_private(mock_get_client):
    """Entity returned by both private search and public search should appear only once."""
    entity = _make_entity(entity_id="1", visibility="public", owner_id="alice")
    mock_get_client.search_entities.return_value = [entity]
    mock_get_client.get_public_entities.return_value = [entity]

    result = get_entities(task="some task", include_public=True)

    # Should appear exactly once (no duplicate)
    assert result.count("tip content") == 1


# ── backward compatibility ─────────────────────────────────────────────────────


def test_create_entity_backward_compat_no_visibility_args(mock_get_client):
    """Calling create_entity without new args should still work and default to private."""
    mock_get_client.update_entities.return_value = [EntityUpdate(id="1", type="fact", content="x", event="ADD", metadata={})]
    result = json.loads(create_entity(content="x", entity_type="fact"))
    assert "error" not in result
    entities = mock_get_client.update_entities.call_args[1]["entities"]
    assert entities[0].metadata.get("visibility") == "private"
