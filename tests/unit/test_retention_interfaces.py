"""Cross-interface retention edits, errors, and execution against real storage."""

import asyncio
import json

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from fastmcp import Client

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.frontend.api.memory import MemoryScope, build_memory_router
from altk_evolve.frontend.mcp import mcp_server
from altk_evolve.retention import RetentionError
from altk_evolve.schema.core import Entity

pytestmark = pytest.mark.unit


@pytest.fixture
def interfaces(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLVE_RETENTION_STORE_PATH", raising=False)
    client = EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))
    monkeypatch.setattr(mcp_server, "get_client", lambda: client)
    app = FastAPI()

    # Test-only authentication provider: production hosts verify credentials here.
    def scope(x_namespace: str = Header("a"), x_agent: str = Header("agent-a"), x_manage: str = Header("yes")):
        return MemoryScope(namespace_id=x_namespace, user_id="alice", agent_id=x_agent, can_manage=x_manage == "yes")

    app.include_router(build_memory_router(client_dependency=lambda: client, scope_dependency=scope))
    with TestClient(app) as http:
        yield client, http


def mcp_call(tool_name, **kwargs):
    async def call():
        async with Client(mcp_server.mcp) as transport:
            result = await transport.call_tool_mcp(tool_name, kwargs)
            assert not result.isError
            return json.loads(result.content[0].text)

    return asyncio.run(call())


@pytest.mark.e2e
def test_policy_rules_and_schedule_lifecycle_across_interfaces(interfaces):
    client, http = interfaces
    service = client.retention("a", agent_id="agent-a")
    service.create_policy("p")
    assert client.namespace_exists("a")
    added = mcp_call("add_retention_rule", namespace_id="a", policy_id="p", name="old", rule={"max_age_days": 90, "action": "delete"})
    assert added["policy"]["rules"][0]["name"] == "old"
    assert http.patch("/manage/retention/policies/p/rules/old", json={"changes": {"max_age_days": 180}}).status_code == 200
    assert service.list_rules("p")["items"][0]["max_age_days"] == 180
    definition = {"policy_id": "p", "agent_id": "agent-a", "spec": {"schedule": "0 2 * * *", "timeZone": "America/Los_Angeles"}}
    response = http.post("/manage/retention/schedules", json={"schedule_id": "nightly", "definition": definition})
    assert response.status_code == 201
    assert response.json()["initiated_by"] == "alice"
    shown = mcp_call("get_retention_schedule", namespace_id="a", schedule_id="nightly")
    assert shown["definition"] == service.get_schedule("nightly")["definition"]
    assert len(shown["next_runs"]) == 5
    assert http.post("/manage/retention/schedules/nightly/stop", json={"expected_revision": 1}).status_code == 200
    assert service.get_schedule("nightly")["next_runs"] == []
    resumed = mcp_call("start_retention_schedule", namespace_id="a", schedule_id="nightly", initiated_by="alice", expected_revision=2)
    assert resumed["revision"] == 3 and not resumed["definition"]["spec"]["suspend"]
    assert http.post("/manage/retention/schedules/nightly/stop", json={"expected_revision": 1}).status_code == 409
    with pytest.raises(RetentionError) as stale:
        service.stop_schedule("nightly", initiated_by="alice", expected_revision=1)
    assert stale.value.status == 409
    assert (
        "conflict"
        in mcp_call("stop_retention_schedule", namespace_id="a", schedule_id="nightly", initiated_by="alice", expected_revision=1)["error"]
    )
    assert http.delete("/manage/retention/policies/p").status_code == 409
    service.delete_schedule("nightly", expected_revision=3)
    mcp_call("remove_retention_rule", namespace_id="a", policy_id="p", name="old")
    assert service.list_rules("p")["items"] == []
    assert http.delete("/manage/retention/policies/p").status_code == 200
    assert "not found" in mcp_call("get_retention_policy", namespace_id="a", policy_id="p")["error"]


def test_shared_errors_authentication_and_scope(interfaces):
    client, http = interfaces
    service = client.retention("a", agent_id="agent-a")
    service.create_policy("p")
    assert client.namespace_exists("a")
    assert http.post("/manage/retention/policies", json={"policy_id": "p"}).status_code == 409
    assert http.patch("/manage/retention/policies/missing", json={"enabled": False}).status_code == 404
    assert http.get("/manage/retention/policies/p", headers={"x-namespace": "b"}).status_code == 404
    assert http.post("/manage/retention/policies/p/rules", json={"name": "bad", "rule": {"max_age_dayz": 90}}).status_code == 400
    assert http.post("/manage/retention/policies/p/rules", json={"name": "bad", "rule": {"max_age_days": -1}}).status_code == 400
    assert http.post("/manage/retention/policies", json={"policy_id": "other"}, headers={"x-manage": "no"}).status_code == 403
    service.create_schedule("nightly", {"policy_id": "p", "agent_id": "agent-a", "spec": {"schedule": "@daily"}}, initiated_by="alice")
    assert (
        http.post("/manage/retention/schedules/nightly/start", json={"expected_revision": 1}, headers={"x-agent": "agent-b"}).status_code
        == 404
    )
    assert (
        http.patch("/manage/retention/schedules/nightly", json={"changes": {"agent_id": "agent-b"}, "expected_revision": 1}).status_code
        == 403
    )
    assert service.get_schedule("nightly")["revision"] == 1
    assert (
        http.post("/manage/retention/schedules/nightly/stop", json={"expected_revision": 1, "initiated_by": "impostor"}).status_code == 422
    )
    assert "not found" in mcp_call("get_retention_schedule", namespace_id="b", schedule_id="nightly")["error"]


def test_execution_is_independent_of_mcp_and_has_scoped_audit(interfaces, monkeypatch):
    client, http = interfaces
    service = client.retention("a", agent_id="agent-a")
    service.create_policy("p")
    assert client.namespace_exists("a")
    service.add_rule("p", "old", {"max_age_days": 0, "action": "delete"})
    for namespace, agent in [("a", "agent-a"), ("a", "agent-b"), ("b", "agent-a")]:
        client.ensure_namespace(namespace)
        client.update_entities(namespace, [Entity(type="fact", content="private content", metadata={"agent_id": agent})], False)

    def unavailable(*args, **kwargs):
        raise AssertionError("Programmatic and REST execution must not invoke MCP tools")

    monkeypatch.setattr(mcp_server, "run_retention", unavailable)
    preview = service.run("p", initiated_by="alice")
    assert len(preview["deleted"]) == 1
    assert len(client.scan_entities("a")) == 2
    response = http.post("/manage/retention/runs", json={"policy_id": "p", "dry_run": False})
    assert response.status_code == 200
    assert "private content" not in response.text
    assert len(client.scan_entities("a")) == 1 and len(client.scan_entities("b")) == 1
    report = service.get_run(response.json()["run_id"])
    assert report["status"] == "completed" and report["initiated_by"] == "alice"
    assert http.get(f"/manage/retention/runs/{report['run_id']}", headers={"x-agent": "agent-b"}).status_code == 404
    with pytest.raises(RetentionError) as forbidden:
        service.run("p", metadata_filters={"agent_id": "agent-b"})
    assert forbidden.value.status == 403


@pytest.mark.e2e
def test_structured_mcp_catalog_and_execution_inputs(interfaces):
    client, _ = interfaces
    created = mcp_call("create_retention_policy", namespace_id="a", policy_id="p")
    assert created["policy_id"] == "p"
    assert "already exists" in mcp_call("create_retention_policy", namespace_id="a", policy_id="p")["error"]
    assert mcp_call("update_retention_policy", namespace_id="a", policy_id="p", name="Renamed")["name"] == "Renamed"
    policy = {"rules": [{"name": "old", "max_age_days": 90}]}
    assert mcp_call("validate_retention_policy", policy=policy)["valid"]
    assert mcp_call("put_retention_policy", namespace_id="a", policy_id="p", name="P", policy=policy)["policy"]["rules"][0]["name"] == "old"
    definition = {"policy_id": "p", "spec": {"schedule": "@daily"}}
    assert (
        mcp_call("create_retention_schedule", namespace_id="a", schedule_id="s", initiated_by="alice", definition=definition)["revision"]
        == 1
    )
    changed = mcp_call(
        "update_retention_schedule",
        namespace_id="a",
        schedule_id="s",
        initiated_by="alice",
        expected_revision=1,
        changes={"spec": {"timeZone": "America/Los_Angeles"}},
    )
    assert changed["definition"]["spec"]["schedule"] == "@daily"
    assert "referenced" in mcp_call("delete_retention_policy", namespace_id="a", policy_id="p")["error"]
    report = mcp_call(
        "run_retention",
        namespace_id="a",
        policy_id="p",
        initiated_by="alice",
        metadata_filters={"agent_id": "agent-a"},
        additional_matches=[],
    )
    assert report["dry_run"]
    assert mcp_call("get_retention_run", namespace_id="a", run_id=report["run_id"])["initiated_by"] == "alice"
    client.retention("a").delete_schedule("s", expected_revision=2)
    assert mcp_call("delete_retention_policy", namespace_id="a", policy_id="p")["deleted"]


def test_execution_failure_preserves_audit_without_exposing_exception_text(interfaces, monkeypatch):
    client, http = interfaces
    service = client.retention("a")
    service.create_policy("p")
    assert client.namespace_exists("a")

    def failed_scan(*args, **kwargs):
        raise RuntimeError("private entity content in backend exception")

    monkeypatch.setattr(client, "scan_entities", failed_scan)
    result = http.post("/manage/retention/runs", json={"policy_id": "p"})
    assert result.status_code == 500
    assert "private entity content" not in result.text
    report = service.get_run(result.json()["detail"]["run_id"])
    assert report["status"] == "failed"
    assert report["report"]["failure"]["type"] == "RuntimeError"


def test_manual_retry_reuses_run_without_repeating_work(interfaces, monkeypatch):
    client, http = interfaces
    service = client.retention("a", agent_id="agent-a")
    service.put_policy("p", name="P", policy={"rules": [{"name": "old", "max_age_days": 1, "action": "delete"}]})
    calls = []

    def fail_scan(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("PRIVATE provider failure")

    monkeypatch.setattr(client, "scan_entities", fail_scan)
    body = {"policy_id": "p", "dry_run": True, "run_id": "manual-retry-test"}
    first = http.post("/manage/retention/runs", json=body)
    assert first.status_code == 500
    assert first.json()["detail"]["run_id"] == body["run_id"]
    second = http.post("/manage/retention/runs", json=body)
    assert second.status_code == 409
    assert second.json()["detail"]["run_id"] == body["run_id"]
    assert len(calls) == 1
    runs = http.get("/manage/retention/runs").json()["items"]
    assert len(runs) == 1
    assert runs[0]["run_id"] == body["run_id"]
    assert "PRIVATE" not in str(first.json()) + str(second.json()) + str(runs)


def test_manual_success_is_replayed_and_key_cannot_change_scope(interfaces):
    client, http = interfaces
    client.retention("a", agent_id="agent-a").create_policy("p")
    body = {"policy_id": "p", "dry_run": True, "run_id": "successful-operation"}
    first = http.post("/manage/retention/runs", json=body)
    assert first.status_code == 200, first.text
    second = http.post("/manage/retention/runs", json=body)
    assert second.status_code == 200
    assert second.json() == first.json()
    changed = http.post("/manage/retention/runs", json={**body, "dry_run": False})
    assert changed.status_code == 409


def test_cascade_does_not_persist_source_task_text(interfaces):
    client, http = interfaces
    service = client.retention("a", agent_id="agent-a")
    service.put_policy(
        "p",
        name="P",
        policy={"rules": [{"name": "old", "entity_type": "trajectory", "max_age_days": 0, "action": "delete", "cascade_derived": True}]},
    )
    private = "PRIVATE customer escalation details"
    entities = [
        Entity(type="trajectory", content=private, metadata={"trace_id": private, "agent_id": "agent-a", "user_id": "alice"}),
        Entity(type="guideline", content=private, metadata={"source_task_id": private, "agent_id": "agent-a", "user_id": "alice"}),
    ]
    for entity in entities:
        client.update_entities("a", [entity], enable_conflict_resolution=False)
    response = http.post("/manage/retention/runs", json={"policy_id": "p", "dry_run": False})
    assert response.status_code == 200, response.text
    report = response.json()
    assert len(report["deleted"]) == 2
    assert any(item["reason"] == "cascade" for item in report["deleted"])
    stored = service.store.get_run(namespace_id="a", run_id=report["run_id"])
    assert private not in json.dumps(stored)
    assert private not in response.text
