from types import SimpleNamespace

import pytest

from altk_evolve.retention.store import RetentionStore

pytestmark = pytest.mark.unit


def _client(tmp_path):
    backend = SimpleNamespace(data_dir=tmp_path)
    return SimpleNamespace(config=SimpleNamespace(backend="filesystem"), backend=backend)


def test_retention_store_persists_policy_catalog_and_run_history(tmp_path):
    store = RetentionStore(_client(tmp_path))
    policy = store.put_policy(
        namespace_id="service-instance-a",
        policy_id="standard",
        name="Standard retention",
        description="Default lifecycle policy",
        enabled=True,
        policy={"rules": [{"name": "stale", "max_age_days": 90}]},
    )
    store.put_policy(
        namespace_id="service-instance-b",
        policy_id="other",
        name="Other namespace",
        description=None,
        enabled=True,
        policy={"rules": []},
    )
    store.save_run(
        namespace_id="service-instance-a",
        run_id="run-a",
        policy_id="standard",
        agent_id="agent-a",
        initiated_by="operator-a",
        status="completed",
        report={"run_id": "run-a", "deleted": []},
        created_at="2026-09-09T12:00:00+00:00",
    )
    store.save_run(
        namespace_id="service-instance-a",
        run_id="run-b",
        policy_id="standard",
        agent_id="agent-b",
        initiated_by="operator-b",
        status="completed",
        report={"run_id": "run-b", "deleted": []},
        created_at="2026-09-09T13:00:00+00:00",
    )

    assert policy["policy_id"] == "standard"
    assert [item["policy_id"] for item in store.list_policies(namespace_id="service-instance-a")] == ["standard"]
    assert store.get_policy(namespace_id="service-instance-b", policy_id="standard") is None
    assert [item["run_id"] for item in store.list_runs(namespace_id="service-instance-a", agent_id="agent-a")] == ["run-a"]


def test_retention_store_updates_policy_without_replacing_created_at(tmp_path):
    store = RetentionStore(_client(tmp_path))
    original = store.put_policy(
        namespace_id="service-instance-a",
        policy_id="standard",
        name="Original",
        description=None,
        enabled=True,
        policy={"rules": []},
    )
    updated = store.put_policy(
        namespace_id="service-instance-a",
        policy_id="standard",
        name="Updated",
        description="Changed",
        enabled=False,
        policy={"rules": []},
    )

    assert updated["name"] == "Updated"
    assert updated["enabled"] is False
    assert updated["created_at"] == original["created_at"]


def test_competing_manual_requests_reserve_one_durable_run(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    stores = [RetentionStore(_client(tmp_path)) for _ in range(4)]

    def claim(store):
        return store.claim_run(
            namespace_id="a", run_id="same", request_hash="request", policy_id="p", agent_id="agent", initiated_by="admin"
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, stores))
    assert sum(claimed for claimed, _ in results) == 1
    assert {saved for _, saved in results} == {"request"}
    # Even a crash before execution leaves a record that can be reconciled.
    record = RetentionStore(_client(tmp_path)).get_run(namespace_id="a", run_id="same")
    assert record["status"] == "running"
    assert record["agent_id"] == "agent"
    assert record["initiated_by"] == "admin"
    assert stores[0].claim_run(
        namespace_id="b", run_id="same", request_hash="request", policy_id="p", agent_id="agent", initiated_by="admin"
    )[0]
