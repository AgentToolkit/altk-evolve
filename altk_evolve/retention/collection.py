"""PostgreSQL mark-and-sweep retention with transactional deletion receipts.

Marks contain references and PostgreSQL row versions, never memory contents. Every sweep
locks the current entity and records its outcome in the deletion transaction.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from types import SimpleNamespace
from typing import Any

from altk_evolve.retention.engine import RetentionEngine
from altk_evolve.frontend.services.context import cancellation_requested
from altk_evolve.retention.policy import RetentionPolicy
from altk_evolve.retention.schedule_store import ScheduleStore
from altk_evolve.schema.core import RecordedEntity


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Collection:
    def __init__(self, client: Any, namespace: str, agent_id: str | None = None):
        self.client, self.namespace, self.agent_id = client, namespace, agent_id
        self.store = ScheduleStore(client)
        if not self.store._is_postgres:
            raise ValueError("Durable mark and sweep requires the PostgreSQL backend")
        from psycopg import sql

        self.table = sql.Identifier(client.backend._table_name(namespace))
        with self.store.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('evolve_retention_collection_schema'))")
            conn.execute("""CREATE TABLE IF NOT EXISTS evolve_retention_candidates (
                namespace_id TEXT NOT NULL, policy_id TEXT NOT NULL, entity_id TEXT NOT NULL,
                agent_id TEXT, entity_type TEXT NOT NULL, fingerprint TEXT NOT NULL,
                policy_fingerprint TEXT NOT NULL, rule TEXT NOT NULL, reason TEXT NOT NULL,
                dependencies JSONB NOT NULL, status TEXT NOT NULL, marked_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL, initiated_by TEXT, run_id TEXT NOT NULL,
                PRIMARY KEY(namespace_id,policy_id,entity_id))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS evolve_retention_audit (
                event_id TEXT PRIMARY KEY, namespace_id TEXT NOT NULL, policy_id TEXT NOT NULL,
                entity_id TEXT NOT NULL, agent_id TEXT, outcome TEXT NOT NULL, rule TEXT NOT NULL,
                reason TEXT NOT NULL, run_id TEXT NOT NULL, initiated_by TEXT, policy_revision TEXT NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
            conn.execute("""CREATE TABLE IF NOT EXISTS evolve_retention_mark_cursors (
                namespace_id TEXT NOT NULL,policy_id TEXT NOT NULL,agent_id TEXT NOT NULL,
                last_id BIGINT NOT NULL,PRIMARY KEY(namespace_id,policy_id,agent_id))""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS evolve_retention_audit_scope ON evolve_retention_audit(namespace_id,agent_id,occurred_at)"
            )

    def run(self, policy_id: str, *, initiated_by: str | None, run_id: str | None = None, limit: int = 1000) -> dict[str, Any]:
        run_id = run_id or str(uuid.uuid4())
        started = dt.datetime.now(dt.UTC).isoformat()
        policy = self.store.get_policy(namespace_id=self.namespace, policy_id=policy_id)
        if not policy:
            raise ValueError("Retention policy not found")
        result: dict[str, Any] = {
            "run_id": run_id,
            "namespace_id": self.namespace,
            "policy_id": policy_id,
            "policy_name": policy["name"],
            "initiated_by": initiated_by,
            "started_at": started,
            "dry_run": False,
            "flagged": [],
            "deleted": [],
            "skipped": [],
            "marked": [],
            "errors": [],
            "warnings": [],
        }

        def save(status: str) -> None:
            self.store.save_run(
                namespace_id=self.namespace,
                run_id=run_id,
                policy_id=policy_id,
                agent_id=self.agent_id,
                initiated_by=initiated_by,
                status=status,
                report=result,
                created_at=started,
            )

        save("running")
        try:
            marked = self.mark(policy_id, initiated_by=initiated_by, run_id=run_id, limit=limit)
            result["marked"] = [item for item in marked["marked"] if item["outcome"] == "marked"]
            result["flagged"] = [item for item in marked["marked"] if item["outcome"] == "flagged"]
            if marked["scan_limit_reached"]:
                result["warnings"].append("Marking scan limit reached; more memories may remain")
            swept = self.sweep(policy_id, initiated_by=initiated_by, run_id=run_id, limit=limit)
            result["deleted"] = [item for item in swept["items"] if item["outcome"] == "deleted"]
            result["skipped"] = [item for item in swept["items"] if item["outcome"] != "deleted"]
            result["completed_at"] = dt.datetime.now(dt.UTC).isoformat()
            result["summary"] = (
                f"{len(result['marked'])} marked; {len(result['deleted'])} deleted; {len(result['skipped'])} retained or unavailable"
            )
            result["cancelled"] = marked.get("cancelled", False) or swept.get("cancelled", False)
            save("cancelled" if result["cancelled"] else "completed")
        except Exception:
            result["errors"] = ["Retention interrupted; committed candidate and audit records are preserved"]
            save("interrupted")
            raise
        return result

    @staticmethod
    def entity(row: Any) -> RecordedEntity:
        from altk_evolve.utils.utils import deserialize_content

        return RecordedEntity(
            id=str(row["id"]),
            type=row["type"],
            content=deserialize_content(row["content"]),
            created_at=dt.datetime.fromtimestamp(row["created_at"], dt.UTC),
            metadata=row["metadata"] or {},
        )

    def policy(self, conn: Any, policy_id: str) -> Any:
        row = conn.execute(
            "SELECT * FROM evolve_retention_policies WHERE namespace_id=%s AND policy_id=%s FOR UPDATE", (self.namespace, policy_id)
        ).fetchone()
        if not row:
            raise ValueError("Retention policy not found")
        return row

    def mark(self, policy_id: str, *, initiated_by: str | None, run_id: str | None = None, limit: int = 1000) -> dict[str, Any]:
        from psycopg import sql
        from psycopg.types.json import Jsonb

        run_id = run_id or str(uuid.uuid4())
        marked = []
        cancelled = False
        # Read-only evaluation. Each candidate commits independently below.
        with self.store.transaction() as conn:
            policy = self.policy(conn, policy_id)
            if not policy["enabled"]:
                raise ValueError("Retention policy is disabled")
            cursor = conn.execute(
                "SELECT last_id FROM evolve_retention_mark_cursors WHERE namespace_id=%s AND policy_id=%s AND agent_id=%s",
                (self.namespace, policy_id, self.agent_id or ""),
            ).fetchone()
            last_id = cursor["last_id"] if cursor else 0
            filters = sql.SQL(" WHERE id>%s AND metadata->>'agent_id'=%s") if self.agent_id else sql.SQL(" WHERE id>%s")
            args = (last_id, self.agent_id, limit) if self.agent_id else (last_id, limit)
            rows = conn.execute(
                sql.SQL("SELECT id,type,content,created_at,metadata,xmin::text AS version FROM {}{} ORDER BY id LIMIT %s").format(
                    self.table, filters
                ),
                args,
            ).fetchall()
            entities = [self.entity(row) for row in rows]
            versions = {str(row["id"]): row["version"] for row in rows}
            now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        parsed = RetentionPolicy.from_mapping(json.loads(policy["policy_json"]))
        if any(rule.cascade_derived for rule in parsed.rules):
            traces = [RetentionEngine(self.client)._trace_id(e) for e in entities if e.type == "trajectory"]
            traces = [trace for trace in traces if trace]
            if traces:
                with self.store.transaction() as conn:
                    derived = conn.execute(
                        sql.SQL(
                            "SELECT id,type,content,created_at,metadata,xmin::text AS version FROM {} WHERE metadata->>'source_task_id'=ANY(%s) AND (%s::text IS NULL OR metadata->>'agent_id'=%s)"
                        ).format(self.table),
                        (traces, self.agent_id, self.agent_id),
                    ).fetchall()
                versions.update({str(row["id"]): row["version"] for row in derived})
                known = {e.id for e in entities}
                entities.extend(self.entity(row) for row in derived if str(row["id"]) not in known)
        engine = RetentionEngine(SimpleNamespace(scan_entities=lambda *a, **kw: entities))
        items = engine.evaluate(self.namespace, parsed, now=now, scan_limit=limit)
        snapshots = {e.id: e for e in entities}
        policy_hash = fingerprint(parsed.model_dump(mode="json"))
        for item in items:
            if cancellation_requested():
                cancelled = True
                break
            candidate_status = "pending" if item.action == "delete" else "review"
            entity = snapshots[item.entity_id]
            dependencies = []
            if item.reason.startswith("cascade:"):
                trace = item.reason.removeprefix("cascade:")
                dependencies = [
                    {"id": e.id, "fingerprint": versions[e.id]}
                    for e in entities
                    if e.type == engine.TRAJECTORY_TYPE and str(engine._trace_id(e)) == trace
                ]
                if not dependencies:
                    continue
            with self.store.transaction() as conn:
                current = self.policy(conn, policy_id)
                if (
                    not current["enabled"]
                    or fingerprint(RetentionPolicy.from_mapping(json.loads(current["policy_json"])).model_dump(mode="json")) != policy_hash
                ):
                    cancelled = True
                    break
                row = conn.execute(
                    """INSERT INTO evolve_retention_candidates
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp(),clock_timestamp(),%s,%s)
                    ON CONFLICT(namespace_id,policy_id,entity_id) DO UPDATE SET
                    fingerprint=EXCLUDED.fingerprint,policy_fingerprint=EXCLUDED.policy_fingerprint,
                    rule=EXCLUDED.rule,reason=EXCLUDED.reason,dependencies=EXCLUDED.dependencies,
                    status=EXCLUDED.status,updated_at=clock_timestamp(),initiated_by=EXCLUDED.initiated_by,run_id=EXCLUDED.run_id
                    WHERE evolve_retention_candidates.status IN ('withdrawn','missing')
                      OR (evolve_retention_candidates.status IN ('pending','held','review','deleted') AND
                         (evolve_retention_candidates.fingerprint<>EXCLUDED.fingerprint OR
                          evolve_retention_candidates.policy_fingerprint<>EXCLUDED.policy_fingerprint))
                    RETURNING *""",
                    (
                        self.namespace,
                        policy_id,
                        entity.id,
                        entity.metadata.get("agent_id"),
                        entity.type,
                        versions[entity.id],
                        policy_hash,
                        item.rule,
                        "cascade" if dependencies else item.reason,
                        Jsonb(dependencies),
                        candidate_status,
                        initiated_by,
                        run_id,
                    ),
                ).fetchone()
                if row:
                    self.event(conn, row, "marked" if candidate_status == "pending" else "flagged", run_id, initiated_by)
                    marked.append(
                        {"entity_id": entity.id, "rule": item.rule, "outcome": "marked" if candidate_status == "pending" else "flagged"}
                    )
        if not cancelled:
            with self.store.transaction() as conn:
                conn.execute(
                    """INSERT INTO evolve_retention_mark_cursors VALUES (%s,%s,%s,%s)
                    ON CONFLICT(namespace_id,policy_id,agent_id) DO UPDATE SET last_id=EXCLUDED.last_id""",
                    (self.namespace, policy_id, self.agent_id or "", int(rows[-1]["id"]) if len(rows) == limit else 0),
                )
        return {"run_id": run_id, "marked": marked, "cancelled": cancelled, "scan_limit_reached": len(rows) == limit}

    def event(self, conn: Any, candidate: Any, outcome: str, run_id: str, initiated_by: str | None) -> None:
        conn.execute(
            """INSERT INTO evolve_retention_audit
            (event_id,namespace_id,policy_id,entity_id,agent_id,outcome,rule,reason,run_id,initiated_by,policy_revision)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                str(uuid.uuid4()),
                self.namespace,
                candidate["policy_id"],
                candidate["entity_id"],
                candidate["agent_id"],
                outcome,
                candidate["rule"],
                candidate["reason"],
                run_id,
                initiated_by,
                candidate["policy_fingerprint"],
            ),
        )

    def sweep(self, policy_id: str, *, initiated_by: str | None, run_id: str | None = None, limit: int = 1000) -> dict[str, Any]:
        run_id = run_id or str(uuid.uuid4())
        outcomes = []
        # A bounded snapshot prevents held candidates spinning forever in this sweep.
        with self.store.transaction() as conn:
            rows = conn.execute(
                """SELECT entity_id FROM evolve_retention_candidates WHERE namespace_id=%s AND policy_id=%s
                AND status IN ('pending','held') AND (%s::text IS NULL OR agent_id=%s) ORDER BY updated_at,entity_id LIMIT %s""",
                (self.namespace, policy_id, self.agent_id, self.agent_id, limit),
            ).fetchall()
        for row in rows:
            if cancellation_requested():
                break
            outcome = self.sweep_one(policy_id, row["entity_id"], run_id, initiated_by)
            if outcome:
                outcomes.append(outcome)
        return {"run_id": run_id, "items": outcomes, "cancelled": cancellation_requested()}

    def sweep_one(self, policy_id: str, entity_id: str, run_id: str, initiated_by: str | None) -> dict[str, Any] | None:
        from psycopg import sql

        with self.store.transaction() as conn:
            policy = self.policy(conn, policy_id)
            candidate = conn.execute(
                """SELECT * FROM evolve_retention_candidates WHERE namespace_id=%s AND policy_id=%s AND entity_id=%s
                AND status IN ('pending','held') AND (%s::text IS NULL OR agent_id=%s) FOR UPDATE SKIP LOCKED""",
                (self.namespace, policy_id, entity_id, self.agent_id, self.agent_id),
            ).fetchone()
            if not candidate:
                return None
            ids = sorted({int(entity_id), *(int(d["id"]) for d in candidate["dependencies"])})
            rows = conn.execute(
                sql.SQL(
                    "SELECT id,type,content,created_at,metadata,xmin::text AS version FROM {} WHERE id=ANY(%s) ORDER BY id FOR UPDATE"
                ).format(self.table),
                (ids,),
            ).fetchall()
            entities = {str(r["id"]): self.entity(r) for r in rows}
            versions = {str(r["id"]): r["version"] for r in rows}
            entity = entities.get(entity_id)
            parsed = RetentionPolicy.from_mapping(json.loads(policy["policy_json"]))
            now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            outcome = "deleted"
            if not entity:
                outcome = "missing"
            elif not policy["enabled"] or fingerprint(parsed.model_dump(mode="json")) != candidate["policy_fingerprint"]:
                outcome = "withdrawn"
            elif any(e.metadata.get("legal_hold") for e in entities.values()):
                outcome = "held"
            elif versions[entity_id] != candidate["fingerprint"]:
                outcome = "withdrawn"
            else:
                for dep in candidate["dependencies"]:
                    parent = entities.get(dep["id"])
                    if parent:
                        if versions[parent.id] != dep["fingerprint"]:
                            outcome = "withdrawn"
                    else:
                        receipt = conn.execute(
                            """SELECT 1 FROM evolve_retention_candidates WHERE namespace_id=%s AND policy_id=%s
                            AND entity_id=%s AND status='deleted' AND fingerprint=%s AND policy_fingerprint=%s""",
                            (self.namespace, policy_id, dep["id"], dep["fingerprint"], candidate["policy_fingerprint"]),
                        ).fetchone()
                        if not receipt:
                            outcome = "withdrawn"
                if not candidate["dependencies"]:
                    engine = RetentionEngine(SimpleNamespace(scan_entities=lambda *a, **kw: [entity]))
                    actions = engine.evaluate(self.namespace, parsed, now=now)
                    if not any(i.entity_id == entity_id and i.action == "delete" for i in actions):
                        outcome = "withdrawn"
            if outcome == "deleted":
                conn.execute(sql.SQL("DELETE FROM {} WHERE id=%s").format(self.table), (int(entity_id),))
            conn.execute(
                "UPDATE evolve_retention_candidates SET status=%s,updated_at=clock_timestamp() WHERE namespace_id=%s AND policy_id=%s AND entity_id=%s",
                (outcome, self.namespace, policy_id, entity_id),
            )
            if outcome != candidate["status"]:
                self.event(conn, candidate, outcome, run_id, initiated_by)
            return {
                "entity_id": entity_id,
                "entity_type": candidate["entity_type"],
                "outcome": outcome,
                "rule": candidate["rule"],
                "reason": "legal_hold" if outcome == "held" else candidate["reason"],
            }

    def list(self, *, audit: bool = False, limit: int = 100) -> dict[str, Any]:
        from psycopg import sql

        table = "evolve_retention_audit" if audit else "evolve_retention_candidates"
        order = "occurred_at" if audit else "updated_at"
        with self.store.transaction() as conn:
            rows = conn.execute(
                sql.SQL("SELECT * FROM {} WHERE namespace_id=%s AND (%s::text IS NULL OR agent_id=%s) ORDER BY {} DESC LIMIT %s").format(
                    sql.Identifier(table), sql.Identifier(order)
                ),
                (self.namespace, self.agent_id, self.agent_id, limit),
            ).fetchall()
        fields = {
            "event_id",
            "policy_revision",
            "entity_id",
            "entity_type",
            "policy_id",
            "agent_id",
            "outcome",
            "status",
            "rule",
            "reason",
            "run_id",
            "initiated_by",
            "occurred_at",
            "marked_at",
            "updated_at",
        }
        return {"items": [{k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in row.items() if k in fields} for row in rows]}
