"""Exercise catalog locking with a real PostgreSQL server and isolated schema."""

import datetime as dt
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.e2e


def test_postgres_concurrent_schedule_claims():
    dsn = os.getenv("EVOLVE_TEST_SCHEDULE_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set EVOLVE_TEST_SCHEDULE_POSTGRES_DSN to a disposable PostgreSQL database")
    import psycopg
    from psycopg import sql
    from altk_evolve.retention.schedule import CronJobSpec, ScheduleDefinition
    from altk_evolve.retention.schedule_store import ScheduleStore

    schema = "schedule_test_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with psycopg.connect(dsn, options=f"-csearch_path={schema}", autocommit=True) as conn:
                client = SimpleNamespace(config=SimpleNamespace(backend="postgres"), backend=SimpleNamespace(conn=conn))
                store = ScheduleStore(client)
                store.create_policy("a", "p", "P")
                with pytest.raises(ValueError, match="already exists"):
                    store.create_policy("a", "p", "duplicate")
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(lambda n: store.edit_rule("a", "p", str(n), "add", {"max_age_days": n}), range(4)))
                assert len(store.get_policy(namespace_id="a", policy_id="p")["policy"]["rules"]) == 4
                store.update_policy("a", "p", "renamed", None)
                assert store.get_policy(namespace_id="a", policy_id="p")["name"] == "renamed"
                store.create_policy("b", "p", "isolated")
                store.delete_policy("b", "p")
                start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
                config = ScheduleDefinition(policy_id="p", spec=CronJobSpec(schedule="* * * * *", concurrencyPolicy="Forbid"))
                store.put("a", "daily", config, "alice", expected_revision=0, now=start)
                with pytest.raises(ValueError, match="referenced"):
                    store.delete_policy("a", "p")
                due = start + dt.timedelta(minutes=1)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    jobs = list(pool.map(lambda _: store.dispatch("a", "daily", due), range(4)))
                assert sum(job is not None for job in jobs) == 1
                job = next(job for job in jobs if job)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    claims = list(pool.map(lambda worker: store.claim("a", job, str(worker), due), range(4)))
                assert sum(claim is not None for claim in claims) == 1
                assert store.jobs("b") == []
                assert store.dispatch("a", "daily", due + dt.timedelta(minutes=1)) is None
                assert store.acknowledge_interrupted("a", job)
                assert store.dispatch("a", "daily", due + dt.timedelta(minutes=1))
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
