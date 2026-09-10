"""Durable schedules and execution claims for SQLite and PostgreSQL."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

from altk_evolve.retention.schedule import ScheduleDefinition, utc
from altk_evolve.retention.store import RetentionStore

ACTIVE = ("queued", "running", "cancelling")


class ScheduleStore(RetentionStore):
    """Serialize scheduling decisions in the database, not in a process-local lock."""

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        if self._is_postgres:
            import psycopg
            from psycopg.rows import dict_row

            # Independent catalog connections avoid sharing transaction ownership
            # with entity operations or other worker threads.
            with psycopg.connect(self._postgres.info.dsn, password=self._postgres.info.password, row_factory=dict_row) as conn:
                yield conn
        else:
            with self._connect_sqlite() as conn:
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute("BEGIN IMMEDIATE")
                yield conn

    def sql(self, conn: Any, statement: str, values: tuple[Any, ...] = ()) -> Any:
        return conn.execute(statement.replace("?", "%s") if self._is_postgres else statement, values)

    def _ensure_schema(self) -> None:
        super()._ensure_schema()
        with self.transaction() as conn:
            self.sql(
                conn,
                """CREATE TABLE IF NOT EXISTS evolve_retention_schedules (
                namespace_id TEXT NOT NULL, schedule_id TEXT NOT NULL, definition_json TEXT NOT NULL,
                revision INTEGER NOT NULL, actor_id TEXT NOT NULL, last_schedule_at TEXT NOT NULL,
                condition TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(namespace_id, schedule_id))""",
            )
            self.sql(
                conn,
                """CREATE TABLE IF NOT EXISTS evolve_retention_jobs (
                namespace_id TEXT NOT NULL, job_id TEXT NOT NULL, schedule_id TEXT NOT NULL,
                definition_json TEXT NOT NULL, actor_id TEXT NOT NULL, scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL, worker_id TEXT, heartbeat_at TEXT, finished_at TEXT, error TEXT,
                PRIMARY KEY(namespace_id, job_id))""",
            )
            self.sql(
                conn,
                """CREATE INDEX IF NOT EXISTS evolve_retention_jobs_schedule_idx
                ON evolve_retention_jobs(namespace_id, schedule_id, status)""",
            )

    @staticmethod
    def record(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["definition"] = json.loads(result.pop("definition_json"))
        return result

    def _locked_schedule(self, conn: Any, namespace: str, schedule_id: str) -> Any:
        suffix = " FOR UPDATE" if self._is_postgres else ""
        return self.sql(
            conn, "SELECT * FROM evolve_retention_schedules WHERE namespace_id=? AND schedule_id=?" + suffix, (namespace, schedule_id)
        ).fetchone()

    def put(
        self,
        namespace: str,
        schedule_id: str,
        definition: ScheduleDefinition,
        actor_id: str,
        *,
        expected_revision: int,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        """Create with revision 0 or replace using optimistic concurrency control."""
        if not namespace.strip() or not schedule_id.strip() or not actor_id.strip():
            raise ValueError("namespace, schedule ID, and actor ID are required")
        timestamp = utc(now or dt.datetime.now(dt.UTC)).isoformat()
        definition.spec.next_time(dt.datetime.fromisoformat(timestamp))
        policy = self.get_policy(namespace_id=namespace, policy_id=definition.policy_id)
        if policy is None:
            raise ValueError("Retention policy not found in this namespace")
        with self.transaction() as conn:
            row = self._locked_schedule(conn, namespace, schedule_id)
            if row is None:
                if expected_revision != 0:
                    raise ValueError("Schedule revision conflict")
                # ON CONFLICT makes simultaneous creates fail deterministically.
                cursor = self.sql(
                    conn,
                    """INSERT INTO evolve_retention_schedules VALUES (?, ?, ?, 1, ?, ?, NULL, ?, ?)
                    ON CONFLICT(namespace_id, schedule_id) DO NOTHING""",
                    (namespace, schedule_id, definition.model_dump_json(), actor_id, timestamp, timestamp, timestamp),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Schedule revision conflict")
            else:
                if row["revision"] != expected_revision:
                    raise ValueError("Schedule revision conflict")
                previous = ScheduleDefinition.model_validate_json(row["definition_json"])
                changed_time = (previous.spec.schedule, previous.spec.timeZone) != (definition.spec.schedule, definition.spec.timeZone)
                self.sql(
                    conn,
                    """UPDATE evolve_retention_schedules SET definition_json=?, revision=revision+1,
                    actor_id=?, last_schedule_at=?, condition=NULL, updated_at=? WHERE namespace_id=? AND schedule_id=?""",
                    (
                        definition.model_dump_json(),
                        actor_id,
                        timestamp if changed_time else row["last_schedule_at"],
                        timestamp,
                        namespace,
                        schedule_id,
                    ),
                )
            return self.record(self._locked_schedule(conn, namespace, schedule_id))

    def get(self, namespace: str, schedule_id: str) -> dict[str, Any] | None:
        with self.transaction() as conn:
            row = self._locked_schedule(conn, namespace, schedule_id)
            return self.record(row) if row else None

    def list_schedules(self, namespace: str | None = None) -> list[dict[str, Any]]:
        """The unscoped form is reserved for the trusted Evolve worker."""
        with self.transaction() as conn:
            rows = self.sql(
                conn,
                "SELECT * FROM evolve_retention_schedules"
                + (" WHERE namespace_id=?" if namespace else "")
                + " ORDER BY namespace_id, schedule_id",
                (namespace,) if namespace else (),
            ).fetchall()
            return [self.record(row) for row in rows]

    def delete(self, namespace: str, schedule_id: str, expected_revision: int) -> bool:
        """Delete a schedule only when no executions remain active."""
        with self.transaction() as conn:
            row = self._locked_schedule(conn, namespace, schedule_id)
            if row is None:
                return False
            if row["revision"] != expected_revision:
                raise ValueError("Schedule revision conflict")
            if self._active(conn, namespace, schedule_id):
                raise ValueError("Suspend the schedule and finish or cancel active jobs before deleting it")
            self.sql(conn, "DELETE FROM evolve_retention_schedules WHERE namespace_id=? AND schedule_id=?", (namespace, schedule_id))
            return True

    def _active(self, conn: Any, namespace: str, schedule_id: str) -> list[Any]:
        return list(
            self.sql(
                conn,
                """SELECT * FROM evolve_retention_jobs WHERE namespace_id=? AND schedule_id=?
            AND status IN ('queued','running','cancelling')""",
                (namespace, schedule_id),
            ).fetchall()
        )

    def dispatch(self, namespace: str, schedule_id: str, now: dt.datetime) -> str | None:
        """Atomically admit the most recent due occurrence; never duplicate a tick."""
        now = utc(now)
        with self.transaction() as conn:
            row = self._locked_schedule(conn, namespace, schedule_id)
            if row is None:
                return None
            definition = ScheduleDefinition.model_validate_json(row["definition_json"])
            due, condition = definition.spec.due_time(dt.datetime.fromisoformat(row["last_schedule_at"]), now)
            self.sql(
                conn,
                "UPDATE evolve_retention_schedules SET condition=? WHERE namespace_id=? AND schedule_id=?",
                (condition, namespace, schedule_id),
            )
            if due is None:
                return None
            active = self._active(conn, namespace, schedule_id)
            if active and definition.spec.concurrencyPolicy == "Forbid":
                return None
            if active and definition.spec.concurrencyPolicy == "Replace":
                self.sql(
                    conn,
                    """UPDATE evolve_retention_jobs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancelling' END
                    WHERE namespace_id=? AND schedule_id=? AND status IN ('queued','running')""",
                    (namespace, schedule_id),
                )
                # Wait for running mutations to acknowledge cancellation before replacement.
                if any(job["status"] != "queued" for job in active):
                    return None
            job_id = str(uuid.uuid4())
            self.sql(
                conn,
                """INSERT INTO evolve_retention_jobs
                (namespace_id,job_id,schedule_id,definition_json,actor_id,scheduled_at,status)
                VALUES (?,?,?,?,?,?,'queued')""",
                (namespace, job_id, schedule_id, row["definition_json"], row["actor_id"], due.isoformat()),
            )
            self.sql(
                conn,
                "UPDATE evolve_retention_schedules SET last_schedule_at=? WHERE namespace_id=? AND schedule_id=?",
                (due.isoformat(), namespace, schedule_id),
            )
            return job_id

    def jobs(self, namespace: str | None = None, *, schedule_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        conditions, values = [], []
        if namespace is not None:
            conditions.append("namespace_id=?")
            values.append(namespace)
        if schedule_id is not None:
            conditions.append("schedule_id=?")
            values.append(schedule_id)
        with self.transaction() as conn:
            rows = self.sql(
                conn,
                "SELECT * FROM evolve_retention_jobs"
                + (" WHERE " + " AND ".join(conditions) if conditions else "")
                + " ORDER BY scheduled_at DESC LIMIT ?",
                tuple(values) + (max(1, min(limit, 1000)),),
            ).fetchall()
            return [self.record(row) for row in rows]

    def claim(self, namespace: str, job_id: str, worker_id: str, now: dt.datetime) -> dict[str, Any] | None:
        with self.transaction() as conn:
            row = self.sql(conn, "SELECT * FROM evolve_retention_jobs WHERE namespace_id=? AND job_id=?", (namespace, job_id)).fetchone()
            if row is None or row["status"] != "queued":
                return None
            definition = ScheduleDefinition.model_validate_json(row["definition_json"])
            deadline = definition.spec.startingDeadlineSeconds
            if deadline is not None and (utc(now) - dt.datetime.fromisoformat(row["scheduled_at"])).total_seconds() > deadline:
                self.sql(
                    conn,
                    "UPDATE evolve_retention_jobs SET status='missed', finished_at=? WHERE namespace_id=? AND job_id=? AND status='queued'",
                    (utc(now).isoformat(), namespace, job_id),
                )
                return None
            cursor = self.sql(
                conn,
                """UPDATE evolve_retention_jobs SET status='running',worker_id=?,heartbeat_at=?
                WHERE namespace_id=? AND job_id=? AND status='queued'""",
                (worker_id, utc(now).isoformat(), namespace, job_id),
            )
            return self.record(row) if cursor.rowcount == 1 else None

    def cancelled(self, namespace: str, job_id: str, worker_id: str) -> bool:
        """Renew the claim at operation boundaries and fail closed on lost ownership."""
        with self.transaction() as conn:
            cursor = self.sql(
                conn,
                """UPDATE evolve_retention_jobs SET heartbeat_at=?
                WHERE namespace_id=? AND job_id=? AND worker_id=? AND status='running'""",
                (dt.datetime.now(dt.UTC).isoformat(), namespace, job_id, worker_id),
            )
            return bool(cursor.rowcount != 1)

    def finish(self, namespace: str, job_id: str, worker_id: str, status: str, error: str | None = None) -> None:
        with self.transaction() as conn:
            self.sql(
                conn,
                """UPDATE evolve_retention_jobs SET status=CASE WHEN status='cancelling' THEN 'cancelled' ELSE ? END,
                finished_at=?,error=? WHERE namespace_id=? AND job_id=? AND worker_id=? AND status IN ('running','cancelling')""",
                (status, dt.datetime.now(dt.UTC).isoformat(), error, namespace, job_id, worker_id),
            )

    def cancel(self, namespace: str, job_id: str) -> bool:
        with self.transaction() as conn:
            cursor = self.sql(
                conn,
                """UPDATE evolve_retention_jobs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancelling' END
                WHERE namespace_id=? AND job_id=? AND status IN ('queued','running')""",
                (namespace, job_id),
            )
            return bool(cursor.rowcount == 1)

    def queued(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.transaction() as conn:
            rows = self.sql(
                conn, "SELECT * FROM evolve_retention_jobs WHERE status='queued' ORDER BY scheduled_at LIMIT ?", (limit,)
            ).fetchall()
            return [self.record(row) for row in rows]

    def acknowledge_interrupted(self, namespace: str, job_id: str) -> bool:
        """Operator recovery after confirming the owning worker has stopped.

        Never auto-retry uncertain destructive work. The next schedule occurrence
        can proceed, while this claim stays visible as interrupted.
        """
        with self.transaction() as conn:
            cursor = self.sql(
                conn,
                """UPDATE evolve_retention_jobs SET status='interrupted',finished_at=?,error=?
                WHERE namespace_id=? AND job_id=? AND status IN ('running','cancelling')""",
                (dt.datetime.now(dt.UTC).isoformat(), "Owning worker stopped; partial effects may exist", namespace, job_id),
            )
            changed = bool(cursor.rowcount == 1)
            if changed:
                self.sql(
                    conn,
                    """UPDATE evolve_retention_runs SET status='interrupted',updated_at=?
                    WHERE namespace_id=? AND run_id=? AND status='running'""",
                    (dt.datetime.now(dt.UTC).isoformat(), namespace, job_id),
                )
            return changed

    def get_job(self, namespace: str, job_id: str) -> dict[str, Any] | None:
        with self.transaction() as conn:
            row = self.sql(conn, "SELECT * FROM evolve_retention_jobs WHERE namespace_id=? AND job_id=?", (namespace, job_id)).fetchone()
            return self.record(row) if row else None
