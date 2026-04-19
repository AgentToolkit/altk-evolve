"""
E2E tests for entity sharing using the real FilesystemEntityBackend.

No mocks — exercises EvolveClient + FilesystemEntityBackend + file I/O end-to-end.
"""

import pytest
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import Entity
from altk_evolve.schema.exceptions import EvolveException

pytestmark = pytest.mark.unit


@pytest.fixture
def client(tmp_path):
    config = EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path)))
    return EvolveClient(config=config)


@pytest.fixture
def ns(client):
    return client.create_namespace("main").id


# ── get_entity_by_id ──────────────────────────────────────────────────────────

def test_get_entity_by_id_returns_entity(client, ns):
    updates = client.update_entities(ns, [Entity(type="guideline", content="be concise")], enable_conflict_resolution=False)
    entity_id = updates[0].id

    found = client.get_entity_by_id(ns, entity_id)
    assert found is not None
    assert found.content == "be concise"
    assert found.id == entity_id


def test_get_entity_by_id_missing_returns_none(client, ns):
    assert client.get_entity_by_id(ns, "99999") is None


# ── patch_entity_metadata ─────────────────────────────────────────────────────

def test_patch_entity_metadata_merges_without_touching_content(client, ns):
    updates = client.update_entities(
        ns, [Entity(type="guideline", content="original content", metadata={"creation_mode": "manual"})],
        enable_conflict_resolution=False,
    )
    entity_id = updates[0].id

    updated = client.patch_entity_metadata(ns, entity_id, {"visibility": "public", "owner_id": "alice"})

    assert updated.content == "original content"
    assert updated.metadata["creation_mode"] == "manual"
    assert updated.metadata["visibility"] == "public"
    assert updated.metadata["owner_id"] == "alice"


def test_patch_entity_metadata_persists_to_disk(client, ns):
    updates = client.update_entities(ns, [Entity(type="guideline", content="tip")], enable_conflict_resolution=False)
    entity_id = updates[0].id

    client.patch_entity_metadata(ns, entity_id, {"visibility": "public"})

    # Re-fetch from disk to confirm persistence
    reloaded = client.get_entity_by_id(ns, entity_id)
    assert reloaded.metadata["visibility"] == "public"


def test_patch_entity_metadata_raises_for_missing_entity(client, ns):
    with pytest.raises(EvolveException, match="not found"):
        client.patch_entity_metadata(ns, "99999", {"visibility": "public"})


# ── publish / unpublish round-trip ────────────────────────────────────────────

def test_publish_makes_entity_appear_in_public_search(client, ns):
    updates = client.update_entities(ns, [Entity(type="guideline", content="use context managers")], enable_conflict_resolution=False)
    entity_id = updates[0].id

    client.patch_entity_metadata(ns, entity_id, {"visibility": "public", "owner_id": "alice"})

    public = client.get_public_entities(entity_type="guideline")
    assert any(e.id == entity_id for e in public)


def test_unpublish_removes_entity_from_public_search(client, ns):
    updates = client.update_entities(ns, [Entity(type="guideline", content="use context managers")], enable_conflict_resolution=False)
    entity_id = updates[0].id

    client.patch_entity_metadata(ns, entity_id, {"visibility": "public"})
    client.patch_entity_metadata(ns, entity_id, {"visibility": "private", "published_at": None})

    public = client.get_public_entities(entity_type="guideline")
    assert not any(e.id == entity_id for e in public)


def test_private_entities_not_returned_by_get_public(client, ns):
    client.update_entities(ns, [Entity(type="guideline", content="private tip")], enable_conflict_resolution=False)

    public = client.get_public_entities(entity_type="guideline")
    assert all(e.metadata.get("visibility") == "public" for e in public)


# ── cross-namespace public discovery ──────────────────────────────────────────

def test_public_entity_in_one_namespace_visible_from_another(client):
    ns_a = client.create_namespace("user_a").id
    ns_b = client.create_namespace("user_b").id

    updates = client.update_entities(
        ns_a, [Entity(type="guideline", content="always write tests")], enable_conflict_resolution=False
    )
    entity_id = updates[0].id
    client.patch_entity_metadata(ns_a, entity_id, {"visibility": "public", "owner_id": "alice"})

    # Private entity in ns_b — should not appear in public results
    client.update_entities(ns_b, [Entity(type="guideline", content="private note")], enable_conflict_resolution=False)

    public = client.get_public_entities(entity_type="guideline")
    contents = [e.content for e in public]
    assert "always write tests" in contents
    assert "private note" not in contents


def test_get_public_entities_filters_by_type(client):
    ns = client.create_namespace("mixed").id

    g_updates = client.update_entities(ns, [Entity(type="guideline", content="guideline text")], enable_conflict_resolution=False)
    client.patch_entity_metadata(ns, g_updates[0].id, {"visibility": "public"})

    f_updates = client.update_entities(ns, [Entity(type="fact", content="fact text")], enable_conflict_resolution=False)
    client.patch_entity_metadata(ns, f_updates[0].id, {"visibility": "public"})

    public_guidelines = client.get_public_entities(entity_type="guideline")
    assert all(e.type == "guideline" for e in public_guidelines)
    assert any(e.content == "guideline text" for e in public_guidelines)
