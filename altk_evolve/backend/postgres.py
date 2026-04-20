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

from altk_evolve.backend.base import BaseEntityBackend, BaseSettings
from altk_evolve.config.postgres import PostgresDBSettings, postgres_db_settings
from altk_evolve.db.sqlite_manager import SQLiteManager
from altk_evolve.schema.core import Namespace, RecordedEntity
from altk_evolve.schema.exceptions import EvolveException, NamespaceNotFoundException
from altk_evolve.utils.utils import deserialize_content

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
    _schema_filter_fields = {"id", "type", "content", "created_at"}
    _metadata_filter_prefix = "metadata."

    def __init__(self, config: BaseSettings | None = None):
        super().__init__(config)
        self._settings = config if isinstance(config, type(postgres_db_settings)) else postgres_db_settings
        self.conn = self._connect_target_db()
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

    def _connect(self, dbname: str) -> psycopg.Connection:
        return psycopg.connect(
            host=self._settings.host,
            port=self._settings.port,
            user=self._settings.user,
            password=self._settings.password,
            dbname=dbname,
            autocommit=True,
        )

    def _is_missing_database_error(self, error: Exception) -> bool:
        # Check for SQL state code 3D000 (invalid catalog name)
        if getattr(error, "sqlstate", None) == "3D000":
            return True
        # Also check error message for database does not exist
        error_msg = str(error).lower()
        return "database" in error_msg and "does not exist" in error_msg

    def _create_database(self) -> None:
        logger.info(
            "Database '%s' not found; attempting bootstrap via '%s'",
            self._settings.dbname,
            self._settings.bootstrap_db,
        )

        # Try multiple common administrative databases as fallbacks
        bootstrap_candidates = [
            self._settings.bootstrap_db,  # User-configured (default: "postgres")
            "template1",  # PostgreSQL default template database
            self._settings.user,  # User's default database (often created automatically)
        ]

        # Remove duplicates while preserving order
        seen = set()
        bootstrap_dbs = []
        for db in bootstrap_candidates:
            if db not in seen:
                seen.add(db)
                bootstrap_dbs.append(db)

        last_error = None
        for bootstrap_db in bootstrap_dbs:
            try:
                logger.debug("Attempting to connect to bootstrap database: %s", bootstrap_db)
                admin_conn = self._connect(bootstrap_db)
                try:
                    with admin_conn.cursor() as cur:
                        cur.execute(sql.SQL("CREATE DATABASE {dbname}").format(dbname=sql.Identifier(self._settings.dbname)))
                    logger.info("Successfully created database '%s' using bootstrap database '%s'", self._settings.dbname, bootstrap_db)
                    return
                except Exception as create_error:
                    # Check if database already exists (race condition with another process)
                    sqlstate = getattr(create_error, "sqlstate", None)
                    if sqlstate == "42P04":  # duplicate_database
                        logger.debug("Database '%s' already exists (created by another process)", self._settings.dbname)
                        return  # Treat as success
                    raise  # Re-raise other errors
                finally:
                    if not admin_conn.closed:
                        admin_conn.close()
            except Exception as e:
                last_error = e
                logger.debug("Failed to use bootstrap database '%s': %s", bootstrap_db, e)
                continue

        # If all bootstrap databases failed, raise the last error with helpful message
        raise EvolveException(
            f"Failed to create database '{self._settings.dbname}'. "
            f"Tried bootstrap databases: {', '.join(bootstrap_dbs)}. "
            f"Last error: {last_error}"
        )

    def _connect_target_db(self) -> psycopg.Connection:
        try:
            return self._connect(self._settings.dbname)
        except Exception as e:
            if not self._settings.auto_create_db or not self._is_missing_database_error(e):
                raise
            self._create_database()
            return self._connect(self._settings.dbname)

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

        where_parts: list[sql.Composable] = []
        params: list[Any] = []
        for key, value in filters.items():
            if value is None:
                continue

            if key in self._schema_filter_fields:
                where_parts.append(sql.SQL("{} = %s").format(sql.Identifier(key)))
                params.append(value)
                continue

            if not key.startswith(self._metadata_filter_prefix):
                accepted_keys = sorted(self._schema_filter_fields)
                logger.error(
                    "Invalid Postgres search filter key %r. Accepted schema keys: %s. "
                    "Metadata filters must be prefixed with %r. filters=%r metadata_key=%r where_parts=%r params=%r",
                    key,
                    accepted_keys,
                    self._metadata_filter_prefix,
                    filters,
                    None,
                    [repr(part) for part in where_parts],
                    params,
                )
                raise ValueError(
                    f"Invalid filter key '{key}'. Accepted schema keys: {accepted_keys}. "
                    f"Metadata filters must use the '{self._metadata_filter_prefix}<key>' form."
                )

            metadata_key = key.split(".", 1)[1]
            where_parts.append(sql.SQL("metadata @> %s::jsonb"))
            params.append(json.dumps({metadata_key: value}))
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
