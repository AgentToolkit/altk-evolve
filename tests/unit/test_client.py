"""
Simple tests for KaizenClient wrapper interface.
"""

import datetime
import pytest

from kaizen.backend.base import BaseEntityBackend
from kaizen.schema.core import Entity, Namespace, RecordedEntity
from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.exceptions import NamespaceNotFoundException, NamespaceAlreadyExistsException
from kaizen.frontend.client.kaizen_client import KaizenClient


@pytest.fixture(scope="module")
def kaizen_client() -> KaizenClient:
    kaizen_client = KaizenClient()
    return kaizen_client


@pytest.mark.unit
def test_health_check(kaizen_client: KaizenClient, monkeypatch):
    # Client should return False derived from API response
    def never_ready(self) -> bool:
        return False

    monkeypatch.setattr(kaizen_client.backend, "ready", never_ready.__get__(kaizen_client, BaseEntityBackend))
    health_status = kaizen_client.ready()
    assert not health_status

    # Client should return True derived from API response
    def always_ready(self) -> bool:
        return True

    monkeypatch.setattr(kaizen_client.backend, "ready", always_ready.__get__(kaizen_client, BaseEntityBackend))
    health_status = kaizen_client.ready()
    assert health_status


@pytest.mark.unit
def test_create_namespace(kaizen_client: KaizenClient, monkeypatch):
    # Client should return a valid `Namespace` object derived from API response
    created_at = datetime.datetime.now(datetime.UTC)

    def create_namespace(self, namespace_id=None) -> Namespace:
        return Namespace(id="foobar", created_at=created_at)

    monkeypatch.setattr(kaizen_client.backend, "create_namespace", create_namespace.__get__(kaizen_client.backend, BaseEntityBackend))
    result = kaizen_client.create_namespace(namespace_id="foobar")
    assert result.id == "foobar"
    assert result.created_at == created_at


@pytest.mark.unit
def test_create_namespace_already_exists(kaizen_client: KaizenClient, monkeypatch):
    # Client should raise an exception
    def create_namespace(self, namespace_id=None) -> Namespace:
        raise NamespaceAlreadyExistsException()

    monkeypatch.setattr(kaizen_client.backend, "create_namespace", create_namespace.__get__(kaizen_client.backend, BaseEntityBackend))
    with pytest.raises(NamespaceAlreadyExistsException):
        kaizen_client.create_namespace(namespace_id="foobar")


@pytest.mark.unit
def test_get_namespace_details(kaizen_client: KaizenClient, monkeypatch):
    # Client should return a valid `Namespace` object derived from API response
    created_at = datetime.datetime.now(datetime.UTC)

    def get_namespace_details(self, namespace_id=None) -> Namespace:
        return Namespace(id="foobar", created_at=created_at)

    monkeypatch.setattr(
        kaizen_client.backend, "get_namespace_details", get_namespace_details.__get__(kaizen_client.backend, BaseEntityBackend)
    )
    result = kaizen_client.get_namespace_details(namespace_id="foobar")
    assert result.id == "foobar"
    assert result.created_at == created_at


@pytest.mark.unit
def test_get_namespace_details_nonexistent(kaizen_client: KaizenClient, monkeypatch):
    # Client should raise an exception
    def get_namespace_details(self, namespace_id=None) -> Namespace:
        raise NamespaceNotFoundException()

    monkeypatch.setattr(
        kaizen_client.backend, "get_namespace_details", get_namespace_details.__get__(kaizen_client.backend, BaseEntityBackend)
    )
    with pytest.raises(NamespaceNotFoundException):
        kaizen_client.get_namespace_details(namespace_id="foobar")


@pytest.mark.unit
def test_search_namespaces(kaizen_client: KaizenClient, monkeypatch):
    # Client should return a valid `Namespace` list derived from API response
    created_at = datetime.datetime.now(datetime.UTC)

    def search_namespaces(self, limit=10) -> list[Namespace]:
        return [Namespace(id="foobar", created_at=created_at)]

    monkeypatch.setattr(kaizen_client.backend, "search_namespaces", search_namespaces.__get__(kaizen_client.backend, BaseEntityBackend))
    result = kaizen_client.search_namespaces()
    assert result[0].id == "foobar"
    assert result[0].created_at == created_at


@pytest.mark.unit
def test_delete_namespace(kaizen_client: KaizenClient, monkeypatch):
    # Function should successfully be called in backend; essentially a no-op test.
    def delete_namespace(self, namespace_id):
        pass

    monkeypatch.setattr(kaizen_client.backend, "delete_namespace", delete_namespace.__get__(kaizen_client.backend, BaseEntityBackend))
    kaizen_client.delete_namespace(namespace_id="foobar")


@pytest.mark.unit
def test_update_entities(kaizen_client: KaizenClient, monkeypatch):
    # Client should return a valid `EntityUpdate` list derived from API response
    def update_entities(self, namespace_id, entity, enable_conflict_resolution=True) -> list[EntityUpdate]:
        return [EntityUpdate(id="1", type="fact", content="User's name is Foobar", event="ADD")]

    monkeypatch.setattr(kaizen_client.backend, "update_entities", update_entities.__get__(kaizen_client.backend, BaseEntityBackend))
    result = kaizen_client.update_entities(namespace_id="foobar", entities=[Entity(type="fact", content="User's name is Foobar.")])
    assert result[0].id == "1"
    assert result[0].content == "User's name is Foobar"
    assert result[0].event == "ADD"


@pytest.mark.unit
def test_search_entities(kaizen_client: KaizenClient, monkeypatch):
    # Client should return a valid `RecordedEntity` list derived from API response
    created_at = datetime.datetime.now(datetime.UTC)

    def search_entities(self, namespace_id, query, filters, limit=10) -> list[RecordedEntity]:
        return [RecordedEntity(id="1", type="fact", created_at=created_at, content="User's name is Foobar.")]

    monkeypatch.setattr(kaizen_client.backend, "search_entities", search_entities.__get__(kaizen_client.backend, BaseEntityBackend))
    result = kaizen_client.search_entities(namespace_id="foobar", query="name")
    assert result[0].id == "1"
    assert result[0].content == "User's name is Foobar."
    assert result[0].created_at == created_at


@pytest.mark.unit
def test_get_all_entities(kaizen_client: KaizenClient, monkeypatch):
    # Client should return a valid `RecordedEntity` list derived from API response
    created_at = datetime.datetime.now(datetime.UTC)

    def search_entities(self, namespace_id, query, filters, limit=10) -> list[RecordedEntity]:
        return [RecordedEntity(id="1", type="fact", created_at=created_at, content="User's name is Foobar.")]

    monkeypatch.setattr(kaizen_client.backend, "search_entities", search_entities.__get__(kaizen_client.backend, BaseEntityBackend))
    result = kaizen_client.search_entities(namespace_id="foobar", query="name")
    assert result[0].id == "1"
    assert result[0].content == "User's name is Foobar."
    assert result[0].created_at == created_at


@pytest.mark.unit
def test_delete_entity(kaizen_client: KaizenClient, monkeypatch):
    # Function should successfully be called in backend; essentially a no-op test.
    def delete_entity_by_id(self, namespace_id, entity_id):
        pass

    monkeypatch.setattr(kaizen_client.backend, "delete_entity_by_id", delete_entity_by_id.__get__(kaizen_client.backend, BaseEntityBackend))
    kaizen_client.delete_entity_by_id(namespace_id="foobar", entity_id="1")
