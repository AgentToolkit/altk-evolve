"""
Simple tests for EvolveClient wrapper interface.
"""

import datetime
import pytest

from evolve.backend.base import BaseEntityBackend
from evolve.schema.core import Entity, Namespace, RecordedEntity
from evolve.schema.conflict_resolution import EntityUpdate
from evolve.schema.exceptions import NamespaceNotFoundException, NamespaceAlreadyExistsException
from evolve.frontend.client.evolve_client import EvolveClient


from evolve.config.evolve import EvolveConfig


@pytest.fixture(scope="module")
def evolve_client() -> EvolveClient:
    config = EvolveConfig(backend="filesystem")
    evolve_client = EvolveClient(config=config)
    return evolve_client


@pytest.mark.unit
def test_health_check(evolve_client: EvolveClient, monkeypatch):
    # Client should return False derived from API response
    def never_ready(self) -> bool:
        return False

    monkeypatch.setattr(evolve_client.backend, "ready", never_ready.__get__(evolve_client, BaseEntityBackend))
    health_status = evolve_client.ready()
    assert not health_status

    # Client should return True derived from API response
    def always_ready(self) -> bool:
        return True

    monkeypatch.setattr(evolve_client.backend, "ready", always_ready.__get__(evolve_client, BaseEntityBackend))
    health_status = evolve_client.ready()
    assert health_status


@pytest.mark.unit
def test_create_namespace(evolve_client: EvolveClient, monkeypatch):
    # Client should return a valid `Namespace` object derived from API response
    created_at = datetime.datetime.now(datetime.UTC)

    def create_namespace(self, namespace_id=None) -> Namespace:
        return Namespace(id="foobar", created_at=created_at)

    monkeypatch.setattr(evolve_client.backend, "create_namespace", create_namespace.__get__(evolve_client.backend, BaseEntityBackend))
    result = evolve_client.create_namespace(namespace_id="foobar")
    assert result.id == "foobar"
    assert result.created_at == created_at


@pytest.mark.unit
def test_create_namespace_already_exists(evolve_client: EvolveClient, monkeypatch):
    # Client should raise an exception
    def create_namespace(self, namespace_id=None) -> Namespace:
        raise NamespaceAlreadyExistsException()

    monkeypatch.setattr(evolve_client.backend, "create_namespace", create_namespace.__get__(evolve_client.backend, BaseEntityBackend))
    with pytest.raises(NamespaceAlreadyExistsException):
        evolve_client.create_namespace(namespace_id="foobar")


@pytest.mark.unit
def test_get_namespace_details(evolve_client: EvolveClient, monkeypatch):
    # Client should return a valid `Namespace` object derived from API response
    created_at = datetime.datetime.now(datetime.UTC)

    def get_namespace_details(self, namespace_id=None) -> Namespace:
        return Namespace(id="foobar", created_at=created_at)

    monkeypatch.setattr(
        evolve_client.backend, "get_namespace_details", get_namespace_details.__get__(evolve_client.backend, BaseEntityBackend)
    )
    result = evolve_client.get_namespace_details(namespace_id="foobar")
    assert result.id == "foobar"
    assert result.created_at == created_at


@pytest.mark.unit
def test_get_namespace_details_nonexistent(evolve_client: EvolveClient, monkeypatch):
    # Client should raise an exception
    def get_namespace_details(self, namespace_id=None) -> Namespace:
        raise NamespaceNotFoundException()

    monkeypatch.setattr(
        evolve_client.backend, "get_namespace_details", get_namespace_details.__get__(evolve_client.backend, BaseEntityBackend)
    )
    with pytest.raises(NamespaceNotFoundException):
        evolve_client.get_namespace_details(namespace_id="foobar")


@pytest.mark.unit
def test_search_namespaces(evolve_client: EvolveClient, monkeypatch):
    # Client should return a valid `Namespace` list derived from API response
    created_at = datetime.datetime.now(datetime.UTC)

    def search_namespaces(self, limit=10) -> list[Namespace]:
        return [Namespace(id="foobar", created_at=created_at)]

    monkeypatch.setattr(evolve_client.backend, "search_namespaces", search_namespaces.__get__(evolve_client.backend, BaseEntityBackend))
    result = evolve_client.search_namespaces()
    assert result[0].id == "foobar"
    assert result[0].created_at == created_at


@pytest.mark.unit
def test_delete_namespace(evolve_client: EvolveClient, monkeypatch):
    # Function should successfully be called in backend; essentially a no-op test.
    def delete_namespace(self, namespace_id):
        pass

    monkeypatch.setattr(evolve_client.backend, "delete_namespace", delete_namespace.__get__(evolve_client.backend, BaseEntityBackend))
    evolve_client.delete_namespace(namespace_id="foobar")


@pytest.mark.unit
def test_update_entities(evolve_client: EvolveClient, monkeypatch):
    # Client should return a valid `EntityUpdate` list derived from API response
    def update_entities(self, namespace_id, entity, enable_conflict_resolution=True) -> list[EntityUpdate]:
        return [EntityUpdate(id="1", type="fact", content="User's name is Foobar", event="ADD")]

    monkeypatch.setattr(evolve_client.backend, "update_entities", update_entities.__get__(evolve_client.backend, BaseEntityBackend))
    result = evolve_client.update_entities(namespace_id="foobar", entities=[Entity(type="fact", content="User's name is Foobar.")])
    assert result[0].id == "1"
    assert result[0].content == "User's name is Foobar"
    assert result[0].event == "ADD"


@pytest.mark.unit
def test_search_entities(evolve_client: EvolveClient, monkeypatch):
    # Client should return a valid `RecordedEntity` list derived from API response
    created_at = datetime.datetime.now(datetime.UTC)

    def search_entities(self, namespace_id, query, filters, limit=10) -> list[RecordedEntity]:
        return [RecordedEntity(id="1", type="fact", created_at=created_at, content="User's name is Foobar.")]

    monkeypatch.setattr(evolve_client.backend, "search_entities", search_entities.__get__(evolve_client.backend, BaseEntityBackend))
    result = evolve_client.search_entities(namespace_id="foobar", query="name")
    assert result[0].id == "1"
    assert result[0].content == "User's name is Foobar."
    assert result[0].created_at == created_at


@pytest.mark.unit
def test_get_all_entities(evolve_client: EvolveClient, monkeypatch):
    # Client should return a valid `RecordedEntity` list derived from API response
    created_at = datetime.datetime.now(datetime.UTC)

    def search_entities(self, namespace_id, query, filters, limit=10) -> list[RecordedEntity]:
        return [RecordedEntity(id="1", type="fact", created_at=created_at, content="User's name is Foobar.")]

    monkeypatch.setattr(evolve_client.backend, "search_entities", search_entities.__get__(evolve_client.backend, BaseEntityBackend))
    result = evolve_client.search_entities(namespace_id="foobar", query="name")
    assert result[0].id == "1"
    assert result[0].content == "User's name is Foobar."
    assert result[0].created_at == created_at


@pytest.mark.unit
def test_delete_entity(evolve_client: EvolveClient, monkeypatch):
    # Function should successfully be called in backend; essentially a no-op test.
    def delete_entity_by_id(self, namespace_id, entity_id):
        pass

    monkeypatch.setattr(evolve_client.backend, "delete_entity_by_id", delete_entity_by_id.__get__(evolve_client.backend, BaseEntityBackend))
    evolve_client.delete_entity_by_id(namespace_id="foobar", entity_id="1")


@pytest.mark.unit
@pytest.mark.parametrize("message", [None, "", "   \t\n"])
def test_store_user_facts_skips_none_empty_or_whitespace(evolve_client: EvolveClient, monkeypatch, message):
    def fail_ensure_namespace(namespace_id: str):
        raise AssertionError("ensure_namespace should not be called for blank messages")

    def fail_update_entities(namespace_id, entities, enable_conflict_resolution=True):
        raise AssertionError("update_entities should not be called for blank messages")

    def fail_extract(messages):
        raise AssertionError("extract_facts_from_messages should not be called for blank messages")

    monkeypatch.setattr(evolve_client, "ensure_namespace", fail_ensure_namespace)
    monkeypatch.setattr(evolve_client, "update_entities", fail_update_entities)
    monkeypatch.setattr("evolve.frontend.client.evolve_client.extract_facts_from_messages", fail_extract)

    result = evolve_client.store_user_facts(namespace_id="foobar", message=message, user_id="u1")

    assert result == []


@pytest.mark.unit
def test_store_user_facts_uses_trimmed_message(evolve_client: EvolveClient, monkeypatch):
    captured: dict = {"ensure_namespace_called": False}

    def ensure_namespace(namespace_id: str):
        captured["ensure_namespace_called"] = True
        return Namespace(id=namespace_id, created_at=datetime.datetime.now(datetime.UTC))

    def extract(messages):
        captured["message_content"] = messages[0]["content"]
        return ["trimmed fact"]

    def update_entities(namespace_id, entities, enable_conflict_resolution=True):
        captured["entity_content"] = entities[0].content if entities else None
        return [EntityUpdate(id="1", type="fact", content="trimmed fact", event="ADD")]

    monkeypatch.setattr(evolve_client, "ensure_namespace", ensure_namespace)
    monkeypatch.setattr(evolve_client, "update_entities", update_entities)
    monkeypatch.setattr("evolve.frontend.client.evolve_client.extract_facts_from_messages", extract)

    result = evolve_client.store_user_facts(namespace_id="foobar", message="  hello world \n", user_id="u1")

    assert captured["ensure_namespace_called"] is True
    assert captured["message_content"] == "hello world"
    assert captured["entity_content"] == "trimmed fact"
    assert result[0].event == "ADD"
