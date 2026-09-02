"""Delete Postgres entities owned by one service instance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql

from altk_evolve.config.postgres import PostgresDBSettings
from altk_evolve.schema.core import SERVICE_INSTANCE_METADATA_KEY


@dataclass(frozen=True)
class ServiceInstancePurgeResult:
    """Summary of a service-instance purge or dry run."""

    service_instance_id: str
    dry_run: bool
    deleted_records: int
    tables: dict[str, int]


def _namespace_tables(conn: Any) -> list[tuple[str, str]]:
    """Discover Evolve namespace tables in the active Postgres schema."""
    with conn.cursor() as cur:
        cur.execute(
            r"""
            SELECT t.table_schema, t.table_name
            FROM information_schema.tables AS t
            WHERE t.table_type = 'BASE TABLE'
              AND t.table_schema = current_schema()
              AND t.table_name LIKE 'ns\_%' ESCAPE '\'
              AND EXISTS (
                  SELECT 1
                  FROM information_schema.columns AS c
                  WHERE c.table_schema = t.table_schema
                    AND c.table_name = t.table_name
                    AND c.column_name = 'metadata'
                    AND c.data_type = 'jsonb'
              )
              AND EXISTS (
                  SELECT 1
                  FROM information_schema.columns AS c
                  WHERE c.table_schema = t.table_schema
                    AND c.table_name = t.table_name
                    AND c.column_name = 'id'
              )
            ORDER BY t.table_schema, t.table_name
            """
        )
        return [(str(schema), str(table)) for schema, table in cur.fetchall()]


def _purge_connection(conn: Any, service_instance_id: str, *, dry_run: bool) -> ServiceInstancePurgeResult:
    tables = _namespace_tables(conn)
    affected: dict[str, int] = {}

    try:
        with conn.cursor() as cur:
            for schema_name, table_name in tables:
                table = sql.Identifier(schema_name, table_name)
                key = table_name if schema_name == "public" else f"{schema_name}.{table_name}"
                if dry_run:
                    cur.execute(
                        sql.SQL("SELECT COUNT(*) FROM {} WHERE metadata ->> %s = %s").format(table),
                        (SERVICE_INSTANCE_METADATA_KEY, service_instance_id),
                    )
                    row = cur.fetchone()
                    affected[key] = int(row[0]) if row else 0
                else:
                    cur.execute(
                        sql.SQL("DELETE FROM {} WHERE metadata ->> %s = %s").format(table),
                        (SERVICE_INSTANCE_METADATA_KEY, service_instance_id),
                    )
                    affected[key] = max(cur.rowcount, 0)

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise

    return ServiceInstancePurgeResult(
        service_instance_id=service_instance_id,
        dry_run=dry_run,
        deleted_records=sum(affected.values()),
        tables=affected,
    )


def purge_service_instance_records(
    service_instance_id: str,
    *,
    dry_run: bool = False,
    settings: PostgresDBSettings | None = None,
) -> ServiceInstancePurgeResult:
    """Delete every attributed entity for ``service_instance_id``.

    This path connects with psycopg directly and intentionally does not create
    an ``EvolveClient`` or load an embedding model. Namespace catalog rows are
    tenant-level metadata and are left intact.
    """
    service_instance_id = (service_instance_id or "").strip()
    if not service_instance_id:
        raise ValueError("service_instance_id is required")

    resolved_settings = settings or PostgresDBSettings()
    conn = psycopg.connect(
        host=resolved_settings.host,
        port=resolved_settings.port,
        user=resolved_settings.user,
        password=resolved_settings.password,
        dbname=resolved_settings.dbname,
        autocommit=False,
    )
    try:
        return _purge_connection(conn, service_instance_id, dry_run=dry_run)
    finally:
        conn.close()
