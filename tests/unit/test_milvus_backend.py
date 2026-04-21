"""
Unit tests for MilvusEntityBackend.
Tests all methods with mocked Milvus client, SQLiteManager, and embedding model.
"""

import datetime
import pytest
from unittest.mock import Mock, MagicMock, patch

from altk_evolve.backend.milvus import MilvusEntityBackend, parse_milvus_entity
from altk_evolve.schema.core import Entity, Namespace, RecordedEntity
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.exceptions import NamespaceNotFoundException, EvolveException


@pytest.fixture(scope="module")
def milvus_backend() -> MilvusEntityBackend:
    """Create a MilvusEntityBackend instance for testing."""
    with patch("altk_evolve.backend.milvus.MilvusClient"), patch("altk_evolve.backend.milvus.SentenceTransformer"):
        backend = MilvusEntityBackend()
        return backend


@pytest.fixture
def db_manager():
    """Create a mock SQLiteManager for testing."""

    def create_namespace(namespace_id: str) -> Namespace:
        return Namespace(id=namespace_id, created_at=datetime.datetime.now(datetime.UTC))

    manager = MagicMock()
    manager.__enter__ = Mock(return_value=manager)
    manager.__exit__ = Mock(return_value=False)
    manager.create_namespace = create_namespace
    return manager


def always_has_collection(collection_name: str):
    return True


def never_has_collection(collection_name: str):
    return False


def noop_create_collection(collection_name: str, dimension, auto_id, schema):
    pass


def arbitrary_namespace(namespace_id: str) -> Namespace:
    return Namespace(id=namespace_id, created_at=datetime.datetime.now(datetime.UTC))


def arbitrary_collection_stats(collection_name: str):
    return {"row_count": 42}


def arbitrary_embedding(text: str):
    return [0.1] * 384


@pytest.mark.unit
def test_ready(milvus_backend: MilvusEntityBackend, monkeypatch):
    """Test the ready() health check method."""

    def list_collections():
        return ["collection1", "collection2"]

    monkeypatch.setattr(milvus_backend.milvus, "list_collections", list_collections)
    assert milvus_backend.ready()


@pytest.mark.unit
def test_create_namespace(milvus_backend: MilvusEntityBackend, db_manager, monkeypatch):
    """Test creating a new namespace."""
    namespace_id = "test_namespace"
    monkeypatch.setattr(milvus_backend.milvus, "has_collection", never_has_collection)
    monkeypatch.setattr(milvus_backend.milvus, "create_collection", noop_create_collection)

    with patch("altk_evolve.backend.milvus.SQLiteManager", return_value=db_manager):
        result = milvus_backend.create_namespace(namespace_id=namespace_id)

    assert result.id == namespace_id
    assert isinstance(result.created_at, datetime.datetime)

    # create a namespace with auto-generated id
    with patch("altk_evolve.backend.milvus.SQLiteManager", return_value=db_manager):
        result = milvus_backend.create_namespace()

    assert result.id.startswith("ns_")
    assert isinstance(result.created_at, datetime.datetime)


@pytest.mark.unit
def test_get_namespace_details(milvus_backend: MilvusEntityBackend, db_manager, monkeypatch):
    """Test retrieving namespace details."""
    monkeypatch.setattr(milvus_backend.milvus, "has_collection", never_has_collection)

    # Test nonexistent namespace
    with pytest.raises(NamespaceNotFoundException):
        milvus_backend.get_namespace_details(namespace_id="nonexistent_namespace")

    monkeypatch.setattr(milvus_backend.milvus, "has_collection", always_has_collection)
    monkeypatch.setattr(milvus_backend.milvus, "get_collection_stats", arbitrary_collection_stats)
    db_manager.get_namespace = arbitrary_namespace

    # Test existing namespace
    with patch("altk_evolve.backend.milvus.SQLiteManager", return_value=db_manager):
        result = milvus_backend.get_namespace_details(namespace_id="test_namespace")

    assert result.id == "test_namespace"
    assert isinstance(result.created_at, datetime.datetime)
    assert result.num_entities == 42


@pytest.mark.unit
def test_search_namespaces(milvus_backend: MilvusEntityBackend, db_manager, monkeypatch):
    """Test searching for namespaces."""
    created_at = datetime.datetime.now(datetime.UTC)

    db_manager.search_namespaces = Mock(
        return_value=[Namespace(id="namespace1", created_at=created_at), Namespace(id="namespace2", created_at=created_at)]
    )

    monkeypatch.setattr(milvus_backend.milvus, "get_collection_stats", arbitrary_collection_stats)

    with patch("altk_evolve.backend.milvus.SQLiteManager", return_value=db_manager):
        result = milvus_backend.search_namespaces(limit=10)

    assert len(result) == 2
    assert result[0].id == "namespace1"
    assert result[0].num_entities == 42
    assert result[1].id == "namespace2"
    assert result[1].num_entities == 42


@pytest.mark.unit
def test_delete_namespace(milvus_backend: MilvusEntityBackend, db_manager, monkeypatch):
    """Test deleting a namespace."""
    namespace_id = "test_namespace"
    drop_collection = Mock()
    db_manager.delete_namespace = Mock()
    monkeypatch.setattr(milvus_backend.milvus, "drop_collection", drop_collection)

    with patch("altk_evolve.backend.milvus.SQLiteManager", return_value=db_manager):
        milvus_backend.delete_namespace(namespace_id=namespace_id)

    drop_collection.assert_called_once_with(collection_name=namespace_id)
    db_manager.delete_namespace.assert_called_once_with(namespace_id)


@pytest.mark.unit
def test_update_entities(milvus_backend: MilvusEntityBackend, monkeypatch):
    """Test updating entities."""
    entity_update = EntityUpdate(id="12345", type="Test entity content", content="fact", event="ADD")

    # No potential conflicts to resolve
    def search_entities(self, namespace_id, query, filters=None, limit=10):
        return []

    def insert(collection_name, data):
        return {"ids": [12345]}

    def resolve_conflicts(old_entities, new_entities):
        return [entity_update]

    monkeypatch.setattr(milvus_backend.milvus, "has_collection", always_has_collection)
    monkeypatch.setattr(milvus_backend.milvus, "insert", insert)
    monkeypatch.setattr(milvus_backend.embedding_model, "encode", arbitrary_embedding)
    monkeypatch.setattr(milvus_backend, "search_entities", search_entities.__get__(milvus_backend, MilvusEntityBackend))

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", resolve_conflicts):
        entities = [Entity(type=entity_update.type, content=entity_update.content, metadata={"key": "value"})]
        result = milvus_backend.update_entities(namespace_id="test_namespace", entities=entities, enable_conflict_resolution=True)

    assert len(result) == 1
    assert result[0] == entity_update


@pytest.mark.unit
def test_update_entities_mixed_types_raises_exception(milvus_backend: MilvusEntityBackend, monkeypatch):
    """Test that updating entities with mixed types raises an exception."""
    monkeypatch.setattr(milvus_backend.milvus, "has_collection", always_has_collection)

    with pytest.raises(EvolveException, match="All entities must have the same type"):
        milvus_backend.update_entities(
            namespace_id="test_namespace",
            entities=[Entity(type="fact", content="Content 1"), Entity(type="guideline", content="Content 2")],
            enable_conflict_resolution=False,
        )


@pytest.mark.unit
def test_search_entities(milvus_backend: MilvusEntityBackend, monkeypatch):
    """Test searching entities with a query string."""

    def query(collection_name, filter="", output_fields=None, timeout=None, ids=None, partition_names=None, **kwargs):
        return [
            {
                "id": 123,
                "type": "fact",
                "content": "Test content",
                "created_at": int(datetime.datetime.now(datetime.UTC).timestamp()),
                "metadata": {},
            }
        ]

    def search(collection_name, data, filter=None, limit=None, output_fields=None, search_params=None, **kwargs):
        # This mocks the vector search
        return [
            [
                {
                    "id": 123,
                    "type": "fact",
                    "content": "Test content",
                    "created_at": int(datetime.datetime.now(datetime.UTC).timestamp()),
                    "metadata": {},
                    "distance": 0.0,
                }
            ]
        ]

    monkeypatch.setattr(milvus_backend.milvus, "has_collection", always_has_collection)
    monkeypatch.setattr(milvus_backend.milvus, "query", query)
    monkeypatch.setattr(milvus_backend.milvus, "search", search)
    monkeypatch.setattr(milvus_backend.embedding_model, "encode", arbitrary_embedding)

    # Test searching entities with a query (list all).
    result = milvus_backend.search_entities(namespace_id="test_namespace", query="test query", limit=10)

    assert len(result) == 1
    assert result[0].id == "123"
    assert result[0].type == "fact"
    assert result[0].content == "Test content"

    # Test searching entities without a query (list all).
    result_2: list[RecordedEntity] = milvus_backend.search_entities(namespace_id="test_namespace", query=None)

    assert len(result_2) == 1
    assert result_2[0].id == "123"
    assert result_2[0].type == "fact"
    assert result_2[0].content == "Test content"

    # Test searching entities with filters.
    result_3 = milvus_backend.search_entities(namespace_id="test_namespace", query="test_query", filters={"type": "fact"}, limit=10)

    assert len(result_3) == 1
    assert result_3[0].id == "123"
    assert result_3[0].type == "fact"
    assert result_3[0].content == "Test content"


@pytest.mark.unit
def test_split_filters_skips_none_values(milvus_backend: MilvusEntityBackend):
    """Test _split_filters ignores None values while preserving schema/metadata routing."""
    schema_filters, metadata_filters = milvus_backend._split_filters(
        {
            "type": "trajectory",
            "created_at": None,
            "metadata.task_id": "123",
            "metadata.source_task_id": None,
            "task_id": "123",
            "source_span_id": None,
        }
    )

    assert schema_filters == {"type": "trajectory"}
    assert metadata_filters == {"task_id": "123"}


@pytest.mark.unit
@pytest.mark.parametrize("filter_value", [1700000000, "1700000000", 1700000000000, "1700000000000"])
def test_entity_matches_filter_normalizes_created_at(filter_value):
    entity = RecordedEntity(
        id="1",
        type="trajectory",
        content="message",
        metadata={},
        created_at=datetime.datetime.fromtimestamp(1700000000, datetime.UTC),
    )

    assert MilvusEntityBackend._entity_matches_filter(entity, {"created_at": filter_value}, {})


@pytest.mark.unit
def test_entity_matches_filter_created_at_rejects_non_numeric_filter():
    entity = RecordedEntity(
        id="1",
        type="trajectory",
        content="message",
        metadata={},
        created_at=datetime.datetime.fromtimestamp(1700000000, datetime.UTC),
    )

    assert not MilvusEntityBackend._entity_matches_filter(entity, {"created_at": "not-an-epoch"}, {})


@pytest.mark.unit
def test_search_entities_filters_metadata_in_python(milvus_backend: MilvusEntityBackend, monkeypatch):
    """Test metadata filtering is applied even if backend returns mixed records."""

    def query(collection_name, filter="", output_fields=None, timeout=None, ids=None, partition_names=None, **kwargs):
        now = int(datetime.datetime.now(datetime.UTC).timestamp())
        return [
            {
                "id": 1,
                "type": "trajectory",
                "content": "message one",
                "created_at": now,
                "metadata": {"task_id": "123"},
            },
            {
                "id": 2,
                "type": "guideline",
                "content": "guideline",
                "created_at": now,
                "metadata": {"source_task_id": "123"},
            },
            {
                "id": 3,
                "type": "trajectory",
                "content": "message two",
                "created_at": now,
                "metadata": {"task_id": "other"},
            },
        ]

    monkeypatch.setattr(milvus_backend.milvus, "has_collection", always_has_collection)
    monkeypatch.setattr(milvus_backend.milvus, "query", query)

    result = milvus_backend.search_entities(
        namespace_id="test_namespace",
        query=None,
        filters={"type": "trajectory", "task_id": "123"},
        limit=100,
    )

    assert len(result) == 1
    assert result[0].id == "1"
    assert result[0].type == "trajectory"
    assert (result[0].metadata or {}).get("task_id") == "123"


@pytest.mark.unit
def test_search_entities_overfetches_before_python_filter(milvus_backend: MilvusEntityBackend, monkeypatch):
    """Test filtered queries still return matches when backend ignores filter and truncates by limit."""

    def query(collection_name, filter="", output_fields=None, timeout=None, ids=None, partition_names=None, limit=10, **kwargs):
        now = int(datetime.datetime.now(datetime.UTC).timestamp())
        records = [
            {
                "id": i,
                "type": "trajectory",
                "content": f"trajectory {i}",
                "created_at": now,
                "metadata": {"task_id": "123"},
            }
            for i in range(1, 20)
        ]
        records.extend(
            [
                {
                    "id": 100 + i,
                    "type": "guideline",
                    "content": f"guideline {i}",
                    "created_at": now,
                    "metadata": {"source_task_id": "123"},
                }
                for i in range(1, 6)
            ]
        )
        # Simulate backend truncation before applying filter.
        return records[:limit]

    monkeypatch.setattr(milvus_backend.milvus, "has_collection", always_has_collection)
    monkeypatch.setattr(milvus_backend.milvus, "query", query)

    result = milvus_backend.search_entities(
        namespace_id="test_namespace",
        query=None,
        filters={"type": "guideline"},
        limit=10,
    )

    assert len(result) == 5
    assert all(entity.type == "guideline" for entity in result)


@pytest.mark.unit
def test_delete_entity_by_id(milvus_backend: MilvusEntityBackend, monkeypatch):
    """Test deleting an entity by ID."""
    delete = Mock()

    monkeypatch.setattr(milvus_backend.milvus, "has_collection", always_has_collection)
    monkeypatch.setattr(milvus_backend.milvus, "delete", delete)

    milvus_backend.delete_entity_by_id(namespace_id="test_namespace", entity_id="12345")

    # Milvus uses integers for its IDs, so the backend converted it.
    delete.assert_called_once_with(collection_name="test_namespace", ids=[12345])


@pytest.mark.unit
def test_delete_entity_nonexistent_namespace(milvus_backend: MilvusEntityBackend, monkeypatch):
    """Test deleting an entity from a non-existent namespace."""
    monkeypatch.setattr(milvus_backend.milvus, "has_collection", never_has_collection)

    with pytest.raises(NamespaceNotFoundException):
        milvus_backend.delete_entity_by_id(namespace_id="nonexistent_namespace", entity_id="12345")


# ── update_entity_metadata ────────────────────────────────────────────────────


@pytest.mark.unit
def test_update_entity_metadata_merges_and_returns(milvus_backend: MilvusEntityBackend, monkeypatch):
    """Fetches via milvus.query, merges patch, upserts with partial_update, returns updated entity."""
    now_ts = int(datetime.datetime.now(datetime.UTC).timestamp())
    raw_entity = {
        "id": 42,
        "type": "guideline",
        "content": "be concise",
        "created_at": now_ts,
        "metadata": {"creation_mode": "manual"},
    }

    query = Mock(return_value=[raw_entity])
    upsert = Mock(return_value={"ids": [42]})
    flush = Mock()
    load = Mock()

    monkeypatch.setattr(milvus_backend.milvus, "has_collection", always_has_collection)
    monkeypatch.setattr(milvus_backend.milvus, "query", query)
    monkeypatch.setattr(milvus_backend.milvus, "upsert", upsert)
    monkeypatch.setattr(milvus_backend.milvus, "flush", flush)
    monkeypatch.setattr(milvus_backend.milvus, "load_collection", load)
    monkeypatch.setattr(milvus_backend.embedding_model, "encode", arbitrary_embedding)

    result = milvus_backend.update_entity_metadata("test_namespace", "42", {"visibility": "public", "owner_id": "alice"})

    # query must use direct id filter, not search_entities fan-out
    query.assert_called_once_with(
        collection_name="test_namespace",
        filter="id == 42",
        output_fields=["id", "type", "content", "created_at", "metadata"],
        limit=1,
    )

    # upsert must carry merged metadata
    upserted_data = upsert.call_args[1]["data"]
    assert upserted_data["metadata"]["creation_mode"] == "manual"
    assert upserted_data["metadata"]["visibility"] == "public"
    assert upserted_data["metadata"]["owner_id"] == "alice"

    assert result.metadata["visibility"] == "public"
    assert result.metadata["creation_mode"] == "manual"


@pytest.mark.unit
def test_update_entity_metadata_raises_for_missing_entity(milvus_backend: MilvusEntityBackend, monkeypatch):
    """Raises EvolveException when milvus.query returns no results."""
    monkeypatch.setattr(milvus_backend.milvus, "has_collection", always_has_collection)
    monkeypatch.setattr(milvus_backend.milvus, "query", Mock(return_value=[]))

    with pytest.raises(EvolveException, match="not found"):
        milvus_backend.update_entity_metadata("test_namespace", "99", {"visibility": "public"})


@pytest.mark.unit
def test_update_entity_metadata_rejects_non_numeric_id(milvus_backend: MilvusEntityBackend):
    """Raises EvolveException immediately for non-numeric entity IDs."""
    with pytest.raises(EvolveException, match="must be numeric"):
        milvus_backend.update_entity_metadata("test_namespace", "not-an-id", {"visibility": "public"})


@pytest.mark.unit
def test_parse_milvus_entity_accepts_epoch_zero_created_at():
    parsed = parse_milvus_entity(
        {
            "id": 1,
            "type": "fact",
            "content": "Test content",
            "created_at": 0,
            "metadata": {},
        }
    )

    assert parsed.created_at == datetime.datetime.fromtimestamp(0, datetime.UTC)
