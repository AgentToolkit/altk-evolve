import datetime
import json
import logging
import uuid
from collections.abc import Callable, Sequence
from typing import Any

import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from evolve.backend.base import BaseEntityBackend, BaseSettings
from evolve.config.postgres import PostgresDBSettings, postgres_db_settings
from evolve.db.sqlite_manager import SQLiteManager
from evolve.schema.core import Namespace, RecordedEntity
from evolve.schema.exceptions import EvolveException, NamespaceNotFoundException
from evolve.utils.utils import deserialize_content

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("entities-db.pgvector")


def _entity_row_factory(cursor: psycopg.Cursor[Any]) -> Callable[[Sequence[Any]], RecordedEntity]:
    """Row factory that produces RecordedEntity instances directly from query results."""
    cols = [col.name for col in cursor.description] if cursor.description else []

    def make_row(values: Sequence[Any]) -> RecordedEntity:
        row = dict(zip(cols, values))
        return RecordedEntity(
            id=str(row["id"]),
            type=row["type"],
            content=deserialize_content(row["content"]),
            created_at=datetime.datetime.fromtimestamp(row["created_at"], datetime.UTC),
            metadata=row.get("metadata", {}),
        )

    return make_row


class PostgresEntityBackend(BaseEntityBackend):
    conn: psycopg.Connection
    embedding_model: SentenceTransformer
    embedding_dim: int
    _settings: PostgresDBSettings

    def __init__(self, config: BaseSettings | None = None):
        super().__init__(config)
        self._settings = config if isinstance(config, type(postgres_db_settings)) else postgres_db_settings
        self.conn = psycopg.connect(
            host=self._settings.host,
            port=self._settings.port,
            user=self._settings.user,
            password=self._settings.password,
            dbname=self._settings.dbname,
            autocommit=True,
        )
        try:
            self._ensure_pgvector_extension()
            register_vector(self.conn)
            self.embedding_model = SentenceTransformer(self._settings.embedding_model)
            embedding_dim = self.embedding_model.get_sentence_embedding_dimension()
            if embedding_dim is None or embedding_dim <= 0:
                raise EvolveException(
                    f"Embedding model '{self._settings.embedding_model}' reported an invalid dimension: {embedding_dim!r}"
                )
            self.embedding_dim = embedding_dim
        except Exception:
            if not self.conn.closed:
                self.conn.close()
            raise

    def _ensure_pgvector_extension(self):
        """Ensure the pgvector extension is installed."""
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

    def _table_name(self, namespace_id: str) -> str:
        """Return a safe table name for a namespace."""
        return f"ns_{namespace_id}"

    def _table_exists(self, namespace_id: str) -> bool:
        """Check if the table for a namespace exists."""
        table = self._table_name(namespace_id)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                (table,),
            )
            row = cur.fetchone()
            return row[0] if row else False

    def _validate_namespace(self, namespace_id: str):
        if not self._table_exists(namespace_id):
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")

    def ready(self) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True

    def details(self) -> dict:
        """Return details about the backend."""
        return {"backend": "postgres", "host": self._settings.host, "port": self._settings.port}

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        """Create a new namespace (PostgreSQL table) for entities."""
        namespace_id = namespace_id or "ns_" + str(uuid.uuid4()).replace("-", "_")
        table = self._table_name(namespace_id)

        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {table} (
                        id          BIGSERIAL PRIMARY KEY,
                        type        VARCHAR(128) NOT NULL,
                        content     TEXT NOT NULL,
                        created_at  BIGINT NOT NULL,
                        embedding   vector({dim}),
                        metadata    JSONB DEFAULT '{{}}'::jsonb
                    )
                    """
                ).format(table=sql.Identifier(table), dim=sql.Literal(self.embedding_dim))
            )

        with SQLiteManager() as db_manager:
            return db_manager.create_namespace(namespace_id)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        self._validate_namespace(namespace_id)
        table = self._table_name(namespace_id)

        with SQLiteManager() as db_manager:
            namespace = db_manager.get_namespace(namespace_id)
            if namespace is None:
                raise NamespaceNotFoundException(f"Namespace {namespace_id} not found")

            with self.conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT COUNT(*) FROM {table}").format(table=sql.Identifier(table)))
                row = cur.fetchone()
                namespace.num_entities = row[0] if row else 0
            return namespace

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        with SQLiteManager() as db_manager:
            namespaces = []
            for namespace in db_manager.search_namespaces(limit):
                table = self._table_name(namespace.id)
                if self._table_exists(namespace.id):
                    with self.conn.cursor() as cur:
                        cur.execute(sql.SQL("SELECT COUNT(*) FROM {table}").format(table=sql.Identifier(table)))
                        row = cur.fetchone()
                        namespace.num_entities = row[0] if row else 0
                else:
                    namespace.num_entities = 0
                namespaces.append(namespace)
            return namespaces

    def delete_namespace(self, namespace_id: str):
        """Delete a namespace and its table."""
        table = self._table_name(namespace_id)
        with self.conn.cursor() as cur:
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {table}").format(table=sql.Identifier(table)))

        with SQLiteManager() as db_manager:
            db_manager.delete_namespace(namespace_id)

    # ── update_entities hooks ────────────────────────────────────────

    def _add_entity(self, namespace_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> str:
        table = self._table_name(namespace_id)
        embedding = self.embedding_model.encode(content_str).tolist()
        metadata_json = json.dumps(metadata)
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "INSERT INTO {table} (type, content, created_at, embedding, metadata) "
                    "VALUES (%s, %s, %s, %s::vector, %s::jsonb) RETURNING id"
                ).format(table=sql.Identifier(table)),
                (entity_type, content_str, timestamp, str(embedding), metadata_json),
            )
            row = cur.fetchone()
            if row is None:
                raise EvolveException(f"INSERT into namespace '{namespace_id}' returned no row; entity was not created.")
            return str(row[0])

    def _update_entity(self, namespace_id: str, entity_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> None:
        table = self._table_name(namespace_id)
        embedding = self.embedding_model.encode(content_str).tolist()
        metadata_json = json.dumps(metadata)
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "UPDATE {table} SET type = %s, content = %s, created_at = %s, "
                    "embedding = %s::vector, metadata = %s::jsonb WHERE id = %s"
                ).format(table=sql.Identifier(table)),
                (entity_type, content_str, timestamp, str(embedding), metadata_json, int(entity_id)),
            )

    def _delete_entity(self, namespace_id: str, entity_id: str) -> None:
        self.delete_entity_by_id(namespace_id=namespace_id, entity_id=entity_id)

    # ── search / delete ──────────────────────────────────────────────

    def search_entities(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        self._validate_namespace(namespace_id)
        table = self._table_name(namespace_id)
        filters = filters or {}

        where_parts = []
        params: list = []
        for k, v in filters.items():
            where_parts.append(sql.SQL("{} = %s").format(sql.Identifier(k)))
            params.append(v)
        where_clause = sql.SQL(" AND ").join(where_parts) if where_parts else sql.SQL("TRUE")

        if query is None:
            stmt = sql.SQL("SELECT id, type, content, created_at, metadata FROM {table} WHERE {where} LIMIT %s").format(
                table=sql.Identifier(table), where=where_clause
            )
            query_params = params + [limit]
        else:
            query_embedding = self.embedding_model.encode(query).tolist()
            stmt = sql.SQL(
                "SELECT id, type, content, created_at, metadata FROM {table} WHERE {where} ORDER BY embedding <=> %s::vector LIMIT %s"
            ).format(table=sql.Identifier(table), where=where_clause)
            query_params = params + [str(query_embedding), limit]

        with self.conn.cursor(row_factory=_entity_row_factory) as cur:
            cur.execute(stmt, query_params)
            results: list[RecordedEntity] = cur.fetchall()
            return results

    def delete_entity_by_id(self, namespace_id: str, entity_id: str):
        try:
            entity_id_int = int(entity_id)
        except ValueError:
            raise EvolveException(f"Invalid entity ID: {entity_id}. Entity IDs must be numeric.")
        self._validate_namespace(namespace_id)
        table = self._table_name(namespace_id)

        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL("DELETE FROM {table} WHERE id = %s").format(table=sql.Identifier(table)),
                (entity_id_int,),
            )

    def close(self):
        """Close PostgreSQL connection."""
        try:
            if hasattr(self, "conn") and self.conn and not self.conn.closed:
                self.conn.close()
        except Exception as e:
            logger.warning(f"Error closing PostgreSQL connection: {e}")
