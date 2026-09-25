"""Durable retention policy and run storage owned by Evolve."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


class RetentionStore:
    """Persist retention resources beside the configured entity backend.

    PostgreSQL-backed clients use their existing PostgreSQL connection. Other
    backends use a small SQLite catalog, optionally located with
    ``EVOLVE_RETENTION_STORE_PATH``.
    """

    def __init__(self, client: Any, sqlite_path: str | Path | None = None) -> None:
        self._postgres_connection = None
        backend = client.backend
        if client.config.backend == "postgres":
            self._postgres_connection = backend.conn
            self._sqlite_path = None
        else:
            configured_path = sqlite_path or os.getenv("EVOLVE_RETENTION_STORE_PATH")
            if configured_path is None:
                if hasattr(backend, "data_dir"):
                    configured_path = Path(backend.data_dir) / "retention.sqlite.db"
                else:
                    configured_path = getattr(backend, "sqlite_uri", "entities.sqlite.db")
            self._sqlite_path = str(configured_path)
        self._ensure_schema()

    @property
    def _is_postgres(self) -> bool:
        return self._postgres_connection is not None

    @property
    def _postgres(self) -> Any:
        assert self._postgres_connection is not None
        return self._postgres_connection

    def _connect_sqlite(self) -> sqlite3.Connection:
        assert self._sqlite_path is not None
        connection = sqlite3.connect(self._sqlite_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS evolve_retention_requests (
                namespace_id TEXT NOT NULL, run_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                PRIMARY KEY (namespace_id, run_id))""",
            """
            CREATE TABLE IF NOT EXISTS evolve_retention_policies (
                namespace_id TEXT NOT NULL,
                policy_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                enabled BOOLEAN NOT NULL,
                policy_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace_id, policy_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS evolve_retention_runs (
                namespace_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                policy_id TEXT NOT NULL,
                agent_id TEXT,
                initiated_by TEXT,
                status TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace_id, run_id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS evolve_retention_runs_scope_idx
            ON evolve_retention_runs (namespace_id, agent_id, created_at)
            """,
        ]
        if self._is_postgres:
            import psycopg

            with psycopg.connect(self._postgres.info.dsn, password=self._postgres.info.password) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtext('evolve_retention_schema'))")
                for statement in statements:
                    connection.execute(statement)
            return
        with self._connect_sqlite() as connection:
            for statement in statements:
                connection.execute(statement)

    @staticmethod
    def _policy_record(row: Any) -> dict[str, Any]:
        return {
            "namespace_id": row["namespace_id"],
            "policy_id": row["policy_id"],
            "name": row["name"],
            "description": row["description"],
            "enabled": bool(row["enabled"]),
            "policy": _decode_json(row["policy_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _run_record(row: Any) -> dict[str, Any]:
        return {
            "namespace_id": row["namespace_id"],
            "run_id": row["run_id"],
            "policy_id": row["policy_id"],
            "agent_id": row["agent_id"],
            "initiated_by": row["initiated_by"],
            "status": row["status"],
            "report": _decode_json(row["report_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def put_policy(
        self,
        *,
        namespace_id: str,
        policy_id: str,
        name: str,
        description: str | None,
        enabled: bool,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        timestamp = _now()
        values = (
            namespace_id,
            policy_id,
            name,
            description,
            enabled,
            json.dumps(policy),
            timestamp,
            timestamp,
        )
        if self._is_postgres:
            with self._postgres.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO evolve_retention_policies
                    (namespace_id, policy_id, name, description, enabled, policy_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (namespace_id, policy_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        enabled = EXCLUDED.enabled,
                        policy_json = EXCLUDED.policy_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    values,
                )
        else:
            with self._connect_sqlite() as connection:
                connection.execute(
                    """
                    INSERT INTO evolve_retention_policies
                    (namespace_id, policy_id, name, description, enabled, policy_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (namespace_id, policy_id) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        enabled = excluded.enabled,
                        policy_json = excluded.policy_json,
                        updated_at = excluded.updated_at
                    """,
                    values,
                )
        record = self.get_policy(namespace_id=namespace_id, policy_id=policy_id)
        assert record is not None
        return record

    def get_policy(self, *, namespace_id: str, policy_id: str) -> dict[str, Any] | None:
        statement = """
            SELECT namespace_id, policy_id, name, description, enabled, policy_json, created_at, updated_at
            FROM evolve_retention_policies
            WHERE namespace_id = {namespace} AND policy_id = {policy}
        """
        if self._is_postgres:
            with self._postgres.cursor() as cursor:
                cursor.execute(statement.format(namespace="%s", policy="%s"), (namespace_id, policy_id))
                row = cursor.fetchone()
                if row is None:
                    return None
                columns = [column.name for column in cursor.description]
                return self._policy_record(dict(zip(columns, row)))
        with self._connect_sqlite() as connection:
            row = connection.execute(statement.format(namespace="?", policy="?"), (namespace_id, policy_id)).fetchone()
            return self._policy_record(row) if row is not None else None

    def list_policies(self, *, namespace_id: str, include_disabled: bool = False) -> list[dict[str, Any]]:
        predicate = "" if include_disabled else " AND enabled = TRUE"
        statement = f"""
            SELECT namespace_id, policy_id, name, description, enabled, policy_json, created_at, updated_at
            FROM evolve_retention_policies
            WHERE namespace_id = {{namespace}}{predicate}
            ORDER BY name, policy_id
        """
        if self._is_postgres:
            with self._postgres.cursor() as cursor:
                cursor.execute(statement.format(namespace="%s"), (namespace_id,))
                columns = [column.name for column in cursor.description]
                return [self._policy_record(dict(zip(columns, row))) for row in cursor.fetchall()]
        with self._connect_sqlite() as connection:
            rows = connection.execute(statement.format(namespace="?"), (namespace_id,)).fetchall()
            return [self._policy_record(row) for row in rows]

    def claim_run(
        self, *, namespace_id: str, run_id: str, request_hash: str, policy_id: str, agent_id: str | None, initiated_by: str | None
    ) -> tuple[bool, str]:
        """Reserve an operation atomically; duplicate callers never execute it again."""
        sql = """INSERT INTO evolve_retention_requests (namespace_id,run_id,request_hash)
                 VALUES (%s,%s,%s) ON CONFLICT (namespace_id,run_id) DO NOTHING"""
        select = "SELECT request_hash FROM evolve_retention_requests WHERE namespace_id=%s AND run_id=%s"
        started = _now()
        running_sql = """INSERT INTO evolve_retention_runs
            (namespace_id,run_id,policy_id,agent_id,initiated_by,status,report_json,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
        running_values = (
            namespace_id,
            run_id,
            policy_id,
            agent_id,
            initiated_by,
            "running",
            json.dumps({"run_id": run_id, "started_at": started}),
            started,
            started,
        )
        if self._is_postgres:
            import psycopg

            with psycopg.connect(self._postgres.info.dsn, password=self._postgres.info.password) as conn:
                cursor = conn.execute(sql, (namespace_id, run_id, request_hash))
                claimed = cursor.rowcount == 1
                if claimed:
                    conn.execute(running_sql, running_values)
                row = conn.execute(select, (namespace_id, run_id)).fetchone()
                assert row is not None
                saved_hash = row[0]
        else:
            with self._connect_sqlite() as conn:
                sqlite_cursor = conn.execute(sql.replace("%s", "?"), (namespace_id, run_id, request_hash))
                claimed = sqlite_cursor.rowcount == 1
                if claimed:
                    conn.execute(running_sql.replace("%s", "?"), running_values)
                saved_hash = conn.execute(select.replace("%s", "?"), (namespace_id, run_id)).fetchone()[0]
        return claimed, saved_hash

    def save_run(
        self,
        *,
        namespace_id: str,
        run_id: str,
        policy_id: str,
        agent_id: str | None,
        initiated_by: str | None,
        status: str,
        report: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        timestamp = _now()
        values = (
            namespace_id,
            run_id,
            policy_id,
            agent_id,
            initiated_by,
            status,
            json.dumps(report),
            created_at,
            timestamp,
        )
        postgres_sql = """
            INSERT INTO evolve_retention_runs
            (namespace_id, run_id, policy_id, agent_id, initiated_by, status, report_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (namespace_id, run_id) DO UPDATE SET
                policy_id = EXCLUDED.policy_id,
                agent_id = EXCLUDED.agent_id,
                initiated_by = EXCLUDED.initiated_by,
                status = EXCLUDED.status,
                report_json = EXCLUDED.report_json,
                updated_at = EXCLUDED.updated_at
        """
        sqlite_sql = postgres_sql.replace("%s", "?").replace("EXCLUDED.", "excluded.")
        if self._is_postgres:
            with self._postgres.cursor() as cursor:
                cursor.execute(postgres_sql, values)
        else:
            with self._connect_sqlite() as connection:
                connection.execute(sqlite_sql, values)
        record = self.get_run(namespace_id=namespace_id, run_id=run_id)
        assert record is not None
        return record

    def get_run(self, *, namespace_id: str, run_id: str) -> dict[str, Any] | None:
        statement = """
            SELECT namespace_id, run_id, policy_id, agent_id, initiated_by, status, report_json, created_at, updated_at
            FROM evolve_retention_runs
            WHERE namespace_id = {namespace} AND run_id = {run}
        """
        if self._is_postgres:
            with self._postgres.cursor() as cursor:
                cursor.execute(statement.format(namespace="%s", run="%s"), (namespace_id, run_id))
                row = cursor.fetchone()
                if row is None:
                    return None
                columns = [column.name for column in cursor.description]
                return self._run_record(dict(zip(columns, row)))
        with self._connect_sqlite() as connection:
            row = connection.execute(statement.format(namespace="?", run="?"), (namespace_id, run_id)).fetchone()
            return self._run_record(row) if row is not None else None

    def list_runs(
        self,
        *,
        namespace_id: str,
        agent_id: str | None = None,
        policy_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        conditions = ["namespace_id = {namespace}"]
        values: list[Any] = [namespace_id]
        if agent_id is not None:
            conditions.append("agent_id = {agent}")
            values.append(agent_id)
        if policy_id is not None:
            conditions.append("policy_id = {policy}")
            values.append(policy_id)
        values.append(limit)
        statement = f"""
            SELECT namespace_id, run_id, policy_id, agent_id, initiated_by, status, report_json, created_at, updated_at
            FROM evolve_retention_runs
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at DESC
            LIMIT {{limit}}
        """
        if self._is_postgres:
            rendered = statement.format(namespace="%s", agent="%s", policy="%s", limit="%s")
            with self._postgres.cursor() as cursor:
                cursor.execute(rendered, values)
                columns = [column.name for column in cursor.description]
                return [self._run_record(dict(zip(columns, row))) for row in cursor.fetchall()]
        rendered = statement.format(namespace="?", agent="?", policy="?", limit="?")
        with self._connect_sqlite() as connection:
            rows = connection.execute(rendered, values).fetchall()
            return [self._run_record(row) for row in rows]
