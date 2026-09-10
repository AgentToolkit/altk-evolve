"""CronJob timing, durable admission, ownership, and real retention execution."""

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.frontend.services.context import use_client
from altk_evolve.retention.schedule import CronJobSpec, ScheduleDefinition
from altk_evolve.retention.schedule_store import ScheduleStore
from altk_evolve.retention.scheduler import RetentionScheduler
from altk_evolve.schema.core import Entity

pytestmark = pytest.mark.unit
START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLVE_RETENTION_STORE_PATH", raising=False)
    return EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))


@pytest.fixture
def store(client):
    store = ScheduleStore(client)
    for namespace in ("a", "b"):
        client.ensure_namespace(namespace)
        store.put_policy(
            namespace_id=namespace,
            policy_id="p",
            name="policy",
            description=None,
            enabled=True,
            policy={"rules": [{"name": "old", "max_age_days": 0, "action": "delete"}]},
        )
    return store


def definition(concurrency="Forbid", **spec):
    return ScheduleDefinition(
        policy_id="p", spec=CronJobSpec(schedule="* * * * *", concurrencyPolicy=concurrency, **spec), agent_id="agent-a", dry_run=False
    )


@pytest.mark.parametrize("expression", ["* * * * *", "0 2 * * MON-FRI", "@weekly", "0 0 ? JAN MON", "0 0 */2 * *"])
def test_supported_kubernetes_expressions(expression):
    assert CronJobSpec(schedule=expression).next_time(START) > START


@pytest.mark.parametrize(
    "expression", ["0 0 0 * * *", "TZ=UTC 0 0 * * *", "@every 1h", "0 0 L * *", "0 0 * * MON#2", "61 * * * *", "0 0 * * 7"]
)
def test_reject_nonportable_expressions(expression):
    with pytest.raises(ValidationError):
        CronJobSpec(schedule=expression)


def test_timezone_and_deadline_semantics():
    spec = CronJobSpec(schedule="0 2 * * *", timeZone="America/Los_Angeles")
    assert spec.next_time(START) == START.replace(hour=10)
    assert spec.next_time(dt.datetime(2026, 7, 1, tzinfo=dt.UTC)) == dt.datetime(2026, 7, 1, 9, tzinfo=dt.UTC)
    with pytest.raises(ValidationError):
        CronJobSpec(schedule="* * * * *", timeZone="Pacific nonsense")
    spec = CronJobSpec(schedule="* * * * *", startingDeadlineSeconds=10)
    assert spec.due_time(START, START + dt.timedelta(minutes=2, seconds=10))[0] == START + dt.timedelta(minutes=2)
    assert spec.due_time(START, START + dt.timedelta(minutes=2, seconds=11))[0] is None
    assert CronJobSpec(schedule="* * * * *").due_time(START, START + dt.timedelta(minutes=101))[1]


def test_revision_and_namespace_isolation(store):
    row = store.put("a", "daily", definition(), "alice", expected_revision=0, now=START)
    assert row["revision"] == 1
    assert store.get("b", "daily") is None
    with pytest.raises(ValueError, match="revision"):
        store.put("a", "daily", definition(), "alice", expected_revision=0)
    assert store.delete("b", "daily", 1) is False
    assert store.get("a", "daily") is not None


def test_parallel_dispatch_and_claim_are_unique(store):
    store.put("a", "daily", definition(), "alice", expected_revision=0, now=START)
    due = START + dt.timedelta(minutes=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        admitted = list(pool.map(lambda _: store.dispatch("a", "daily", due), range(4)))
    job_id = next(job for job in admitted if job)
    assert sum(job is not None for job in admitted) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda n: store.claim("a", job_id, str(n), due), range(4)))
    assert sum(claim is not None for claim in claims) == 1


@pytest.mark.parametrize("policy", ["Allow", "Forbid", "Replace"])
def test_concurrency_policy(store, policy):
    store.put("a", "daily", definition(policy), "alice", expected_revision=0, now=START)
    first = store.dispatch("a", "daily", START + dt.timedelta(minutes=1))
    assert store.claim("a", first, "worker", START + dt.timedelta(minutes=1))
    second = store.dispatch("a", "daily", START + dt.timedelta(minutes=2))
    if policy == "Allow":
        assert second and second != first
    elif policy == "Forbid":
        assert second is None
        assert not store.cancelled("a", first, "worker")
    else:
        assert second is None
        assert store.cancelled("a", first, "worker")
        store.finish("a", first, "worker", "cancelled")
        assert store.dispatch("a", "daily", START + dt.timedelta(minutes=2))


def test_suspend_resume_preserves_missed_window(store):
    store.put("a", "daily", definition(suspend=True), "alice", expected_revision=0, now=START)
    assert store.dispatch("a", "daily", START + dt.timedelta(minutes=1)) is None
    store.put("a", "daily", definition(), "alice", expected_revision=1, now=START + dt.timedelta(minutes=1))
    assert store.dispatch("a", "daily", START + dt.timedelta(minutes=1))


def test_start_deadline_checked_again_at_claim(store):
    store.put("a", "daily", definition(startingDeadlineSeconds=5), "alice", expected_revision=0, now=START)
    job = store.dispatch("a", "daily", START + dt.timedelta(minutes=1))
    assert store.claim("a", job, "worker", START + dt.timedelta(minutes=2)) is None
    assert store.jobs("a")[0]["status"] == "missed"


def test_worker_uses_policy_agent_scope_and_persists_history(client, store):
    client.update_entities(
        "a",
        [
            Entity(type="fact", content="remove", metadata={"agent_id": "agent-a"}),
            Entity(type="fact", content="keep", metadata={"agent_id": "agent-b"}),
        ],
        False,
    )
    client.update_entities("b", [Entity(type="fact", content="keep other instance", metadata={"agent_id": "agent-a"})], False)
    now = dt.datetime.now(dt.UTC)
    store.put("a", "daily", definition(), "alice", expected_revision=0, now=now - dt.timedelta(minutes=2))
    job_id = store.dispatch("a", "daily", now)
    scheduler = RetentionScheduler(client)
    scheduler.execute(store.jobs("a")[0])
    assert [entity.content for entity in client.scan_entities("a")] == ["keep"]
    assert len(client.scan_entities("b")) == 1
    assert store.jobs("a")[0]["status"] == "completed"
    run = store.get_run(namespace_id="a", run_id=job_id)
    assert run["status"] == "completed"
    assert run["actor_id"] == "alice"
    assert len(run["report"]["deleted"]) == 1
    scheduler.execute(store.jobs("a")[0])  # Finished claim cannot run twice.
    assert store.get_run(namespace_id="a", run_id=job_id) == run


def test_recovery_does_not_retry_uncertain_work(store):
    store.put("a", "daily", definition(), "alice", expected_revision=0, now=START)
    job = store.dispatch("a", "daily", START + dt.timedelta(minutes=1))
    store.claim("a", job, "dead-worker", START)
    assert store.acknowledge_interrupted("b", job) is False
    assert store.acknowledge_interrupted("a", job)
    assert store.cancelled("a", job, "dead-worker")
    assert store.claim("a", job, "new-worker", START) is None
    assert store.dispatch("a", "daily", START + dt.timedelta(minutes=2))


def test_mcp_schedule_round_trip(client, store):
    from altk_evolve.frontend.mcp import mcp_server as mcp

    with use_client(client):
        result = json.loads(mcp.put_retention_schedule("daily", definition().model_dump_json(), "a", "alice"))
        assert result["definition"]["spec"]["concurrencyPolicy"] == "Forbid"
        assert json.loads(mcp.list_retention_schedules("b"))["items"] == []
        assert (
            json.loads(mcp.preview_retention_schedule('{"schedule":"0 2 * * *"}', after=START.isoformat()))["next_runs"][0]
            == START.replace(hour=2).isoformat()
        )


def test_daylight_saving_skips_gap_and_keeps_repeated_occurrences():
    spring = CronJobSpec(schedule="30 2 * * *", timeZone="America/Los_Angeles")
    assert spring.next_time(dt.datetime(2026, 3, 8, 9, tzinfo=dt.UTC)) == dt.datetime(2026, 3, 9, 9, 30, tzinfo=dt.UTC)
    fall = CronJobSpec(schedule="30 1 * * *", timeZone="America/Los_Angeles")
    first = fall.next_time(dt.datetime(2026, 11, 1, 8, tzinfo=dt.UTC))
    second = fall.next_time(first)
    assert first == dt.datetime(2026, 11, 1, 8, 30, tzinfo=dt.UTC)
    assert second == dt.datetime(2026, 11, 1, 9, 30, tzinfo=dt.UTC)


def test_cancellation_stops_between_mutations_and_preserves_run_audit(client, store, monkeypatch):
    from altk_evolve.frontend.mcp import mcp_server
    from altk_evolve.frontend.services.context import execution_cancelled

    client.update_entities("a", [Entity(type="fact", content="one"), Entity(type="fact", content="two")], False)
    deleted = []
    original = client.delete_entity_by_id

    def delete(namespace, entity_id):
        deleted.append(entity_id)
        original(namespace, entity_id)

    monkeypatch.setattr(client, "delete_entity_by_id", delete)
    token = execution_cancelled.set(lambda: len(deleted) == 1)
    try:
        with use_client(client):
            result = json.loads(mcp_server.run_retention("p", dry_run=False, namespace_id="a", actor_id="alice"))
    finally:
        execution_cancelled.reset(token)
    assert result["cancelled"] is True
    assert len(client.scan_entities("a")) == 1
    record = store.get_run(namespace_id="a", run_id=result["run_id"])
    assert record["status"] == "cancelled"
    assert len(record["report"]["deleted"]) == 1


def test_once_worker_drains_multiple_jobs_and_dry_run_preserves_entities(client, store):
    client.update_entities("a", [Entity(type="fact", content="keep", metadata={"agent_id": "agent-a"})], False)
    now = dt.datetime.now(dt.UTC)
    for name in ("one", "two", "three"):
        config = definition().model_copy(update={"dry_run": True})
        store.put("a", name, config, "alice", expected_revision=0, now=now - dt.timedelta(minutes=2))
    RetentionScheduler(client).run(once=True, max_workers=1)
    assert len(client.scan_entities("a")) == 1
    assert len(store.jobs("a")) == 3
    assert all(job["status"] == "completed" for job in store.jobs("a"))


@pytest.mark.e2e
def test_schedule_mcp_transport_and_worker(client, store, monkeypatch):
    import asyncio
    from fastmcp import Client
    from altk_evolve.frontend.mcp import mcp_server

    monkeypatch.setattr(mcp_server, "get_client", lambda: client)

    async def exercise():
        async with Client(mcp_server.mcp) as transport:
            result = await transport.call_tool_mcp(
                "put_retention_schedule",
                {
                    "namespace_id": "a",
                    "schedule_id": "nightly",
                    "actor_id": "alice",
                    "definition": definition().model_dump_json(),
                },
            )
            assert not result.isError
            assert json.loads(result.content[0].text)["revision"] == 1
            result = await transport.call_tool_mcp("list_retention_schedules", {"namespace_id": "b"})
            assert json.loads(result.content[0].text)["items"] == []
            result = await transport.call_tool_mcp(
                "preview_retention_schedule",
                {
                    "spec": '{"schedule":"0 2 * * *","timeZone":"Etc/UTC"}',
                    "after": START.isoformat(),
                },
            )
            assert json.loads(result.content[0].text)["next_runs"][0] == START.replace(hour=2).isoformat()

    asyncio.run(exercise())


def test_service_runtime_joins_scheduler_on_shutdown(client, monkeypatch):
    import threading
    from altk_evolve.retention.scheduler import retention_runtime

    entered = threading.Event()
    finished = threading.Event()

    def run(self, **kwargs):
        entered.set()
        kwargs["stop"].wait(3)
        finished.set()

    monkeypatch.setattr(RetentionScheduler, "run", run)
    with retention_runtime(client):
        assert entered.wait(2)
        assert not finished.is_set()
    assert finished.is_set()


def test_service_runtime_can_be_disabled(client, monkeypatch):
    from altk_evolve.retention.scheduler import retention_runtime

    client.config.retention_scheduler_enabled = False

    def unexpected(self):
        raise AssertionError("Disabled runtime must not initialize scheduler storage")

    monkeypatch.setattr(RetentionScheduler, "__init__", unexpected)
    with retention_runtime(client):
        pass
