"""Real PostgreSQL crash boundaries and independent-connection sweep contention."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from altk_evolve.retention.collection import Collection

pytestmark = pytest.mark.e2e


@pytest.fixture
def collection():
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    dsn = os.getenv("EVOLVE_TEST_SCHEDULE_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set EVOLVE_TEST_SCHEDULE_POSTGRES_DSN to a disposable PostgreSQL database")
    schema = "gc_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with psycopg.connect(dsn, options=f"-csearch_path={schema}", autocommit=True, row_factory=dict_row) as conn:
                client = SimpleNamespace(
                    config=SimpleNamespace(backend="postgres"), backend=SimpleNamespace(conn=conn, _table_name=lambda ns: "ns_" + ns)
                )
                for namespace in ("a", "b"):
                    conn.execute(
                        sql.SQL(
                            "CREATE TABLE {} (id BIGSERIAL PRIMARY KEY,type TEXT,content TEXT,created_at BIGINT,metadata JSONB)"
                        ).format(sql.Identifier("ns_" + namespace))
                    )
                    conn.execute(
                        sql.SQL("INSERT INTO {} (type,content,created_at,metadata) VALUES ('fact','private memory',0,'{{}}')").format(
                            sql.Identifier("ns_" + namespace)
                        )
                    )
                collector = Collection(client, "a")
                collector.store.create_policy("a", "p", "Old memories")
                collector.store.edit_rule("a", "p", "old", "add", {"max_age_days": 1, "action": "delete"})
                yield collector, conn
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_idempotent_mark_and_competing_sweeps(collection):
    collector, conn = collection
    assert len(collector.mark("p", initiated_by="admin")["marked"]) == 1
    assert collector.mark("p", initiated_by="admin")["marked"] == []
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: collector.sweep("p", initiated_by="admin"), range(4)))
    assert sum(item["outcome"] == "deleted" for result in results for item in result["items"]) == 1
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM ns_b").fetchone()["n"] == 1
    receipts = collector.list(audit=True)["items"]
    assert sorted(r["outcome"] for r in receipts) == ["deleted", "marked"]
    assert "private memory" not in str(receipts)


def test_crash_before_deletion_receipt_rolls_back_delete(collection, monkeypatch):
    collector, conn = collection
    collector.mark("p", initiated_by="admin")
    original = collector.event

    def crash(*args, **kwargs):
        raise RuntimeError("pod died after DELETE before audit commit")

    monkeypatch.setattr(collector, "event", crash)
    with pytest.raises(RuntimeError):
        collector.sweep("p", initiated_by="admin")
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 1
    assert collector.list()["items"][0]["status"] == "pending"
    monkeypatch.setattr(collector, "event", original)
    assert collector.sweep("p", initiated_by="admin")["items"][0]["outcome"] == "deleted"
    assert collector.sweep("p", initiated_by="admin")["items"] == []


def test_hold_added_after_mark_prevents_delete(collection):
    collector, conn = collection
    collector.mark("p", initiated_by="admin")
    conn.execute("UPDATE ns_a SET metadata='{" + '"legal_hold":true' + "}'")
    assert collector.sweep("p", initiated_by="admin")["items"][0]["outcome"] == "held"
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 1
    conn.execute("UPDATE ns_a SET metadata='{}'")
    collector.mark("p", initiated_by="admin")
    assert collector.sweep("p", initiated_by="admin")["items"][0]["outcome"] == "deleted"


def test_changed_memory_is_withdrawn_and_policy_disable_blocks_sweep(collection):
    collector, conn = collection
    collector.mark("p", initiated_by="admin")
    conn.execute("UPDATE ns_a SET content='changed'")
    assert collector.sweep("p", initiated_by="admin")["items"][0]["outcome"] == "withdrawn"
    collector.mark("p", initiated_by="admin")
    collector.store.update_policy("a", "p", None, False)
    assert collector.sweep("p", initiated_by="admin")["items"][0]["outcome"] == "withdrawn"
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 1


def test_mark_crash_keeps_previously_committed_candidates(collection, monkeypatch):
    collector, conn = collection
    conn.execute("INSERT INTO ns_a (type,content,created_at,metadata) VALUES ('fact','second',0,'{}')")
    original = collector.event
    calls = 0

    def crash_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("marking interrupted")
        return original(*args, **kwargs)

    monkeypatch.setattr(collector, "event", crash_second)
    with pytest.raises(RuntimeError):
        collector.mark("p", initiated_by="admin")
    assert len(collector.list()["items"]) == 1
    monkeypatch.setattr(collector, "event", original)
    collector.mark("p", initiated_by="admin")
    assert len(collector.list()["items"]) == 2


def test_mark_batches_progress_past_existing_candidates(collection):
    collector, conn = collection
    conn.execute("INSERT INTO ns_a (type,content,created_at,metadata) VALUES ('fact','second',0,'{}'),('fact','third',0,'{}')")
    for _ in range(4):
        collector.mark("p", initiated_by="admin", limit=1)
    assert len(collector.list()["items"]) == 3


def test_flag_policy_never_enters_delete_sweep(collection):
    collector, conn = collection
    collector.store.edit_rule("a", "p", "old", "update", {"action": "flag"})
    collector.mark("p", initiated_by="admin")
    assert collector.list()["items"][0]["status"] == "review"
    assert collector.sweep("p", initiated_by="admin")["items"] == []
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 1


def test_concurrent_hold_transaction_wins_before_sweep(collection):
    import psycopg

    collector, conn = collection
    collector.mark("p", initiated_by="admin")
    with psycopg.connect(conn.info.dsn, password=conn.info.password) as hold:
        hold.execute("UPDATE ns_a SET metadata=jsonb_build_object('legal_hold',true)")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(collector.sweep, "p", initiated_by="admin")
            hold.commit()
            result = future.result(timeout=10)
    assert result["items"][0]["outcome"] == "held"


def test_expired_scheduler_job_unblocks_future_run(collection):
    import datetime as dt
    from altk_evolve.retention.schedule import ScheduleDefinition, CronJobSpec

    collector, conn = collection
    start = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=3)
    definition = ScheduleDefinition(policy_id="p", dry_run=False, spec=CronJobSpec(schedule="* * * * *", concurrencyPolicy="Forbid"))
    collector.store.put("a", "nightly", definition, "admin", expected_revision=0, now=start)
    job = collector.store.dispatch("a", "nightly", start + dt.timedelta(minutes=1))
    collector.store.claim("a", job, "dead-pod", start + dt.timedelta(minutes=1))
    conn.execute("UPDATE evolve_retention_jobs SET heartbeat_at=(clock_timestamp()-interval '2 minutes')::text")
    collector.mark("p", initiated_by="admin")
    collector.store.expire_interrupted_jobs()
    assert collector.store.get_job("a", job)["status"] == "interrupted"
    assert collector.store.dispatch("a", "nightly", dt.datetime.now(dt.UTC))
    assert collector.sweep("p", initiated_by="admin")["items"][0]["outcome"] == "deleted"


def test_cascade_across_scan_pages_and_parent_hold(collection):
    collector, conn = collection
    conn.execute("UPDATE ns_a SET type='trajectory',metadata=jsonb_build_object('trace_id','session-1','legal_hold',true)")
    conn.execute(
        "INSERT INTO ns_a (type,content,created_at,metadata) VALUES ('fact','derived',0,jsonb_build_object('source_task_id','session-1'))"
    )
    collector.store.edit_rule("a", "p", "old", "update", {"entity_type": "trajectory", "cascade_derived": True})
    collector.mark("p", initiated_by="admin", limit=1)
    assert len(collector.list()["items"]) == 2
    assert {item["outcome"] for item in collector.sweep("p", initiated_by="admin")["items"]} == {"held"}
    conn.execute("UPDATE ns_a SET metadata=metadata-'legal_hold' WHERE id=1")
    # A changed version is withdrawn; the next marking pass establishes a fresh version.
    collector.sweep("p", initiated_by="admin")
    collector.mark("p", initiated_by="admin")
    collector.mark("p", initiated_by="admin")
    assert {item["outcome"] for item in collector.sweep("p", initiated_by="admin")["items"]} == {"deleted"}
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 0


def test_rest_and_mcp_share_marks_and_committed_audit(collection):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from altk_evolve.frontend.api.memory import MemoryScope, build_memory_router
    from altk_evolve.frontend.services.context import use_client
    from altk_evolve.frontend.mcp import mcp_server
    import json

    collector, conn = collection
    app = FastAPI()
    app.include_router(
        build_memory_router(
            client_dependency=lambda: collector.client,
            scope_dependency=lambda: MemoryScope(namespace_id="a", user_id="admin", can_manage=True),
        )
    )
    with TestClient(app) as http:
        assert http.post("/manage/retention/policies/p/mark").status_code == 200
        assert http.get("/manage/retention/candidates").json()["items"][0]["status"] == "pending"
        with use_client(collector.client):
            swept = json.loads(mcp_server.sweep_retention("p", "a", initiated_by="admin"))
        assert swept["items"][0]["outcome"] == "deleted"
        audit = http.get("/manage/retention/audit").json()["items"]
        assert {item["outcome"] for item in audit} == {"marked", "deleted"}
        assert all(item["initiated_by"] == "admin" for item in audit)
        assert all(item["policy_revision"] for item in audit)
        assert "private memory" not in str(audit)


def test_connection_loss_before_commit_leaves_no_deletion_receipt(collection, monkeypatch):
    collector, conn = collection
    collector.mark("p", initiated_by="admin")
    original = collector.event

    def disconnect(connection, *args, **kwargs):
        original(connection, *args, **kwargs)
        connection.close()
        raise RuntimeError("connection lost before commit")

    monkeypatch.setattr(collector, "event", disconnect)
    with pytest.raises(RuntimeError):
        collector.sweep("p", initiated_by="admin")
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 1
    assert {item["outcome"] for item in collector.list(audit=True)["items"]} == {"marked"}


def test_concurrent_collectors_can_initialize_schema(collection):
    collector, conn = collection
    conn.execute("DROP TABLE evolve_retention_candidates,evolve_retention_audit,evolve_retention_mark_cursors")
    with ThreadPoolExecutor(max_workers=4) as pool:
        collectors = list(pool.map(lambda _: Collection(collector.client, "a"), range(4)))
    assert all(item.list()["items"] == [] for item in collectors)


def test_source_deletion_requires_exact_scope_and_preserves_holds(collection):
    import datetime as dt
    from altk_evolve.frontend.services.context import use_client
    from altk_evolve.frontend.mcp import mcp_server
    import json

    collector, conn = collection
    collector.store.edit_rule("a", "p", "old", "update", {"source_deleted": True})
    conn.execute("UPDATE ns_a SET metadata=jsonb_build_object('thread_id','thread','user_id','u','agent_id','agent','legal_hold',true)")
    conn.execute(
        "INSERT INTO ns_a (type,content,created_at,metadata) SELECT type,content,created_at,metadata||jsonb_build_object('user_id','other') FROM ns_a"
    )
    assert collector.mark("p", initiated_by="admin")["marked"] == []
    when = (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)).isoformat()
    with use_client(collector.client):
        for _ in range(2):
            assert json.loads(mcp_server.record_source_deletion("a", "thread", "u", "agent", when))["recorded"]
    assert len(collector.mark("p", initiated_by="admin")["marked"]) == 1
    assert collector.sweep("p", initiated_by="admin")["items"][0]["outcome"] == "held"
    assert conn.execute("SELECT count(*) AS n FROM evolve_retention_deleted_sources").fetchone()["n"] == 1
    conn.execute("UPDATE ns_a SET metadata=metadata-'legal_hold' WHERE id=1")
    collector.mark("p", initiated_by="admin")
    assert collector.sweep("p", initiated_by="admin")["items"][0]["outcome"] == "deleted"
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) AS n FROM ns_b").fetchone()["n"] == 1


def test_source_deletion_does_not_match_reused_source_or_changed_provenance(collection):
    import datetime as dt

    collector, conn = collection
    collector.store.edit_rule("a", "p", "old", "update", {"source_deleted": True})
    conn.execute("UPDATE ns_a SET metadata=jsonb_build_object('thread_id','thread','user_id','u','agent_id','agent')")
    collector.record_source_deletion("thread", "u", "agent", (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)).isoformat())
    collector.mark("p", initiated_by="admin")
    conn.execute("UPDATE ns_a SET metadata=metadata||jsonb_build_object('agent_id','other')")
    assert collector.sweep("p", initiated_by="admin")["items"][0]["outcome"] == "withdrawn"
    conn.execute(
        "UPDATE ns_a SET metadata=metadata||jsonb_build_object('agent_id','agent'),created_at=extract(epoch from clock_timestamp()+interval '1 second')::bigint"
    )
    assert collector.mark("p", initiated_by="admin")["marked"] == []


def test_remarking_reassigned_memory_refreshes_candidate_scope(collection):
    collector, conn = collection
    conn.execute('UPDATE ns_a SET metadata=\'{"agent_id":"old-agent"}\'')
    old = Collection(collector.client, "a", "old-agent")
    new = Collection(collector.client, "a", "new-agent")
    old.mark("p", initiated_by="admin")
    conn.execute("UPDATE ns_a SET metadata='{\"agent_id\":\"new-agent\"}',type='guideline'")
    new.mark("p", initiated_by="admin")
    assert old.sweep("p", initiated_by="admin")["items"] == []
    candidate = new.list()["items"][0]
    assert candidate["agent_id"] == "new-agent"
    assert candidate["entity_type"] == "guideline"
    assert new.sweep("p", initiated_by="admin")["items"][0]["outcome"] == "deleted"


def test_source_receipt_excludes_ambiguous_creation_second(collection):
    import datetime as dt

    collector, conn = collection
    second = dt.datetime.now(dt.UTC).replace(microsecond=0) - dt.timedelta(seconds=10)
    conn.execute(
        'UPDATE ns_a SET created_at=%s,metadata=\'{"agent_id":"agent","user_id":"u","thread_id":"thread"}\'',
        (int(second.timestamp()),),
    )
    collector.record_source_deletion("thread", "u", "agent", (second + dt.timedelta(microseconds=100000)).isoformat())
    row = conn.execute("SELECT * FROM ns_a").fetchone()
    assert collector.source_deleted_ids(conn, [collector.entity(row)]) == set()
    conn.execute("UPDATE ns_a SET created_at=created_at-1")
    row = conn.execute("SELECT * FROM ns_a").fetchone()
    assert collector.source_deleted_ids(conn, [collector.entity(row)]) == {"1"}


def test_cascade_keeps_same_trace_owned_by_other_user_or_agent(collection):
    collector, conn = collection
    conn.execute('UPDATE ns_a SET type=\'trajectory\',metadata=\'{"trace_id":"shared","user_id":"alice","agent_id":"a"}\'')
    for user, agent in (("alice", "a"), ("bob", "a"), ("alice", "b")):
        from psycopg.types.json import Jsonb

        conn.execute(
            "INSERT INTO ns_a(type,content,created_at,metadata) VALUES ('guideline','derived',0,%s)",
            (Jsonb({"source_task_id": "shared", "user_id": user, "agent_id": agent}),),
        )
    collector.store.edit_rule("a", "p", "old", "update", {"entity_type": "trajectory", "cascade_derived": True})
    assert {x["entity_id"] for x in collector.mark("p", initiated_by="admin")["marked"]} == {"1", "2"}
    collector.sweep("p", initiated_by="admin")
    assert {row["id"] for row in conn.execute("SELECT id FROM ns_a").fetchall()} == {3, 4}


def test_source_receipt_grace_is_rechecked_during_mark_and_sweep(collection):
    collector, conn = collection
    conn.execute("UPDATE ns_a SET metadata=%s WHERE id=1", ('{"user_id":"alice@example.com","agent_id":"agent","thread_id":"thread"}',))
    collector.store.create_policy("a", "grace", "Source deletion grace")
    collector.store.edit_rule("a", "grace", "orphan", "add", {"source_deleted": True, "min_source_deleted_days": 7, "action": "delete"})
    collector.record_source_deletion(
        "thread", "alice@example.com", "agent", conn.execute("SELECT now() AS now").fetchone()["now"].isoformat()
    )
    assert collector.mark("grace", initiated_by="admin")["marked"] == []
    conn.execute("UPDATE evolve_retention_deleted_sources SET deleted_at=clock_timestamp()-interval '8 days'")
    assert len(collector.mark("grace", initiated_by="admin")["marked"]) == 1
    # Even an existing mark must not bypass a newer source receipt.
    conn.execute("UPDATE evolve_retention_deleted_sources SET deleted_at=clock_timestamp()")
    collector.sweep("grace", initiated_by="admin")
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 1


def test_competing_postgres_manual_claims_leave_one_history_record(collection):
    collector, conn = collection

    def claim(_):
        return collector.store.claim_run(
            namespace_id="a", run_id="manual-operation", request_hash="request", policy_id="p", agent_id="agent", initiated_by="admin"
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, range(4)))
    assert sum(claimed for claimed, _ in results) == 1
    assert {saved for _, saved in results} == {"request"}
    record = conn.execute("SELECT * FROM evolve_retention_runs WHERE namespace_id=%s AND run_id=%s", ("a", "manual-operation")).fetchone()
    assert record["status"] == "running"
    assert record["initiated_by"] == "admin"
