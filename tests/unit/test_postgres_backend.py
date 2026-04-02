"""
Unit tests for PostgresEntityBackend.
Tests all methods with mocked PostgreSQL connection, SQLiteManager, and embedding model.
"""

import datetime
import pytest
from unittest.mock import Mock, MagicMock, patch

from evolve.backend.postgres import PostgresEntityBackend
from evolve.config.postgres import PostgresDBSettings
from evolve.schema.exceptions import EvolveException, NamespaceNotFoundException
from psycopg import sql
from evolve.schema.core import Entity, Namespace, RecordedEntity
from evolve.schema.conflict_resolution import EntityUpdate


@pytest.fixture(scope="module")
def postgres_backend() -> PostgresEntityBackend:
    """Create a PostgresEntityBackend instance with mocked dependencies."""
    with (
        patch("evolve.backend.postgres.psycopg") as mock_psycopg,
        patch("evolve.backend.postgres.register_vector"),
        patch("evolve.backend.postgres.SentenceTransformer") as mock_transformer,
    ):
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_psycopg.connect.return_value = mock_conn
        mock_transformer.return_value.get_sentence_embedding_dimension.return_value = 384
        backend = PostgresEntityBackend()
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


def arbitrary_namespace(namespace_id: str) -> Namespace:
    return Namespace(id=namespace_id, created_at=datetime.datetime.now(datetime.UTC))


def arbitrary_embedding(text: str):
    import numpy as np

    return np.array([0.1] * 384)


def make_table_exists(exists: bool):
    """Return a mock cursor context manager that returns exists for information_schema query."""

    def _table_exists(namespace_id: str) -> bool:
        return exists

    return _table_exists


@pytest.mark.unit
def test_ready(postgres_backend: PostgresEntityBackend):
    """Test the ready() health check method."""
    mock_cursor = MagicMock()
    mock_cursor_context = MagicMock()
    mock_cursor_context.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor_context.__exit__ = Mock(return_value=False)

    with patch.object(postgres_backend.conn, "cursor", return_value=mock_cursor_context):
        assert postgres_backend.ready()
        mock_cursor.execute.assert_called_with("SELECT 1")


@pytest.mark.unit
def test_postgres_backend_initialization_ensures_extension_before_registering_vector():
    call_order: list[str] = []
    config = PostgresDBSettings(
        host="127.0.0.2",
        port=6543,
        user="postgres",
        password="postgres",  # pragma: allowlist secret
        dbname="evolve",
        embedding_model="custom-model",
    )

    with (
        patch("evolve.backend.postgres.psycopg") as mock_psycopg,
        patch("evolve.backend.postgres.register_vector", side_effect=lambda _conn: call_order.append("register_vector")),
        patch("evolve.backend.postgres.SentenceTransformer") as mock_transformer,
        patch.object(
            PostgresEntityBackend,
            "_ensure_pgvector_extension",
            autospec=True,
            side_effect=lambda _self: call_order.append("ensure_extension"),
        ),
    ):
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_psycopg.connect.return_value = mock_conn
        mock_transformer.return_value.get_sentence_embedding_dimension.return_value = 768

        backend = PostgresEntityBackend(config)

    assert call_order == ["ensure_extension", "register_vector"]
    mock_transformer.assert_called_once_with("custom-model")
    assert backend.conn is mock_conn
    assert backend.embedding_dim == 768
    assert backend.details() == {"backend": "postgres", "host": "127.0.0.2", "port": 6543}


@pytest.mark.unit
def test_postgres_backend_initialization_closes_connection_on_setup_failure():
    config = PostgresDBSettings(
        host="127.0.0.2",
        port=6543,
        user="postgres",
        password="postgres",  # pragma: allowlist secret
        dbname="evolve",
        embedding_model="custom-model",
    )

    with (
        patch("evolve.backend.postgres.psycopg") as mock_psycopg,
        patch("evolve.backend.postgres.register_vector", side_effect=RuntimeError("register failed")),
        patch("evolve.backend.postgres.SentenceTransformer"),
        patch.object(PostgresEntityBackend, "_ensure_pgvector_extension", autospec=True),
    ):
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_psycopg.connect.return_value = mock_conn

        with pytest.raises(RuntimeError, match="register failed"):
            PostgresEntityBackend(config)

    mock_conn.close.assert_called_once_with()


@pytest.mark.unit
def test_postgres_backend_initialization_rejects_invalid_embedding_dimension():
    config = PostgresDBSettings(
        host="127.0.0.2",
        port=6543,
        user="postgres",
        password="postgres",  # pragma: allowlist secret
        dbname="evolve",
        embedding_model="custom-model",
    )

    with (
        patch("evolve.backend.postgres.psycopg") as mock_psycopg,
        patch("evolve.backend.postgres.register_vector"),
        patch("evolve.backend.postgres.SentenceTransformer") as mock_transformer,
        patch.object(PostgresEntityBackend, "_ensure_pgvector_extension", autospec=True),
    ):
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_psycopg.connect.return_value = mock_conn
        mock_transformer.return_value.get_sentence_embedding_dimension.return_value = None

        with pytest.raises(EvolveException, match="invalid dimension"):
            PostgresEntityBackend(config)

    mock_conn.close.assert_called_once_with()


@pytest.mark.unit
def test_create_namespace(postgres_backend: PostgresEntityBackend, db_manager, monkeypatch):
    """Test creating a new namespace."""
    namespace_id = "test_namespace"
    monkeypatch.setattr(postgres_backend, "_table_exists", make_table_exists(False))

    mock_cursor = MagicMock()
    mock_cursor_context = MagicMock()
    mock_cursor_context.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor_context.__exit__ = Mock(return_value=False)
    original_literal = sql.Literal

    with (
        patch.object(postgres_backend.conn, "cursor", return_value=mock_cursor_context),
        patch("evolve.backend.postgres.SQLiteManager", return_value=db_manager),
        patch("evolve.backend.postgres.sql.Literal", side_effect=lambda value: original_literal(value)) as mock_literal,
    ):
        result = postgres_backend.create_namespace(namespace_id=namespace_id)

        assert result.id == namespace_id
        assert isinstance(result.created_at, datetime.datetime)

        # create a namespace with auto-generated id
        result = postgres_backend.create_namespace()

        assert result.id.startswith("ns_")
        assert isinstance(result.created_at, datetime.datetime)

    assert any(call.args == (postgres_backend.embedding_dim,) for call in mock_literal.call_args_list)


@pytest.mark.unit
def test_get_namespace_details(postgres_backend: PostgresEntityBackend, db_manager, monkeypatch):
    """Test retrieving namespace details."""
    monkeypatch.setattr(postgres_backend, "_table_exists", make_table_exists(False))

    # Test nonexistent namespace
    with pytest.raises(NamespaceNotFoundException):
        postgres_backend.get_namespace_details(namespace_id="nonexistent_namespace")

    monkeypatch.setattr(postgres_backend, "_table_exists", make_table_exists(True))
    db_manager.get_namespace = arbitrary_namespace

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (42,)
    mock_cursor_context = MagicMock()
    mock_cursor_context.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor_context.__exit__ = Mock(return_value=False)

    # Test existing namespace
    with (
        patch.object(postgres_backend.conn, "cursor", return_value=mock_cursor_context),
        patch("evolve.backend.postgres.SQLiteManager", return_value=db_manager),
    ):
        result = postgres_backend.get_namespace_details(namespace_id="test_namespace")

    assert result.id == "test_namespace"
    assert isinstance(result.created_at, datetime.datetime)
    assert result.num_entities == 42


@pytest.mark.unit
def test_search_namespaces(postgres_backend: PostgresEntityBackend, db_manager, monkeypatch):
    """Test searching for namespaces."""
    created_at = datetime.datetime.now(datetime.UTC)

    db_manager.search_namespaces = Mock(
        return_value=[Namespace(id="namespace1", created_at=created_at), Namespace(id="namespace2", created_at=created_at)]
    )

    monkeypatch.setattr(postgres_backend, "_table_exists", make_table_exists(True))

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (42,)
    mock_cursor_context = MagicMock()
    mock_cursor_context.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor_context.__exit__ = Mock(return_value=False)

    with (
        patch.object(postgres_backend.conn, "cursor", return_value=mock_cursor_context),
        patch("evolve.backend.postgres.SQLiteManager", return_value=db_manager),
    ):
        result = postgres_backend.search_namespaces(limit=10)

    assert len(result) == 2
    assert result[0].id == "namespace1"
    assert result[0].num_entities == 42
    assert result[1].id == "namespace2"
    assert result[1].num_entities == 42


@pytest.mark.unit
def test_delete_namespace(postgres_backend: PostgresEntityBackend, db_manager, monkeypatch):
    """Test deleting a namespace."""
    namespace_id = "test_namespace"
    db_manager.delete_namespace = Mock()

    mock_cursor = MagicMock()
    mock_cursor_context = MagicMock()
    mock_cursor_context.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor_context.__exit__ = Mock(return_value=False)

    with (
        patch.object(postgres_backend.conn, "cursor", return_value=mock_cursor_context),
        patch("evolve.backend.postgres.SQLiteManager", return_value=db_manager),
    ):
        postgres_backend.delete_namespace(namespace_id=namespace_id)

    db_manager.delete_namespace.assert_called_once_with(namespace_id)


@pytest.mark.unit
def test_update_entities(postgres_backend: PostgresEntityBackend, monkeypatch):
    """Test updating entities."""
    entity_update = EntityUpdate(id="12345", type="Test entity content", content="fact", event="ADD")

    def search_entities(self, namespace_id, query, filters=None, limit=10):
        return []

    def resolve_conflicts(old_entities, new_entities):
        return [entity_update]

    monkeypatch.setattr(postgres_backend, "_table_exists", make_table_exists(True))
    monkeypatch.setattr(postgres_backend.embedding_model, "encode", arbitrary_embedding)
    monkeypatch.setattr(postgres_backend, "search_entities", search_entities.__get__(postgres_backend, PostgresEntityBackend))

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (12345,)
    mock_cursor_context = MagicMock()
    mock_cursor_context.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor_context.__exit__ = Mock(return_value=False)

    with (
        patch.object(postgres_backend.conn, "cursor", return_value=mock_cursor_context),
        patch("evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", resolve_conflicts),
    ):
        entities = [Entity(type=entity_update.type, content=entity_update.content, metadata={"key": "value"})]
        result = postgres_backend.update_entities(namespace_id="test_namespace", entities=entities, enable_conflict_resolution=True)

    assert len(result) == 1
    assert result[0].event == "ADD"


@pytest.mark.unit
def test_update_entities_mixed_types_raises_exception(postgres_backend: PostgresEntityBackend, monkeypatch):
    """Test that updating entities with mixed types raises an exception."""
    monkeypatch.setattr(postgres_backend, "_table_exists", make_table_exists(True))

    with pytest.raises(EvolveException, match="All entities must have the same type"):
        postgres_backend.update_entities(
            namespace_id="test_namespace",
            entities=[Entity(type="fact", content="Content 1"), Entity(type="guideline", content="Content 2")],
            enable_conflict_resolution=False,
        )


@pytest.mark.unit
def test_search_entities(postgres_backend: PostgresEntityBackend, monkeypatch):
    """Test searching entities with and without a query string."""
    now_dt = datetime.datetime.now(datetime.UTC)
    sample_rows = [
        RecordedEntity(
            id="123",
            type="fact",
            content="Test content",
            created_at=now_dt,
            metadata={},
        )
    ]

    monkeypatch.setattr(postgres_backend, "_table_exists", make_table_exists(True))
    monkeypatch.setattr(postgres_backend.embedding_model, "encode", arbitrary_embedding)

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = sample_rows
    mock_cursor_context = MagicMock()
    mock_cursor_context.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor_context.__exit__ = Mock(return_value=False)

    with patch.object(postgres_backend.conn, "cursor", return_value=mock_cursor_context):
        # Test with query (vector search)
        result = postgres_backend.search_entities(namespace_id="test_namespace", query="test query", limit=10)
        assert len(result) == 1
        assert result[0].id == "123"
        assert result[0].type == "fact"
        assert result[0].content == "Test content"

        # Test without query (list all)
        result_2 = postgres_backend.search_entities(namespace_id="test_namespace", query=None)
        assert len(result_2) == 1
        assert result_2[0].id == "123"
        assert result_2[0].type == "fact"
        assert result_2[0].content == "Test content"

        # Test with filters
        result_3 = postgres_backend.search_entities(namespace_id="test_namespace", query="test_query", filters={"type": "fact"}, limit=10)
        assert len(result_3) == 1
        assert result_3[0].id == "123"


@pytest.mark.unit
def test_delete_entity_by_id(postgres_backend: PostgresEntityBackend, monkeypatch):
    """Test deleting an entity by ID."""
    monkeypatch.setattr(postgres_backend, "_table_exists", make_table_exists(True))

    mock_cursor = MagicMock()
    mock_cursor_context = MagicMock()
    mock_cursor_context.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor_context.__exit__ = Mock(return_value=False)

    with patch.object(postgres_backend.conn, "cursor", return_value=mock_cursor_context):
        postgres_backend.delete_entity_by_id(namespace_id="test_namespace", entity_id="12345")

    # Verify execute was called (SQL is composed so we check args)
    assert mock_cursor.execute.called


@pytest.mark.unit
def test_delete_entity_nonexistent_namespace(postgres_backend: PostgresEntityBackend, monkeypatch):
    """Test deleting an entity from a non-existent namespace."""
    monkeypatch.setattr(postgres_backend, "_table_exists", make_table_exists(False))

    with pytest.raises(NamespaceNotFoundException):
        postgres_backend.delete_entity_by_id(namespace_id="nonexistent_namespace", entity_id="12345")


@pytest.mark.unit
def test_delete_entity_invalid_id(postgres_backend: PostgresEntityBackend, monkeypatch):
    """Test deleting an entity with a non-numeric ID."""
    with pytest.raises(EvolveException, match="Invalid entity ID"):
        postgres_backend.delete_entity_by_id(namespace_id="test_namespace", entity_id="not_a_number")
