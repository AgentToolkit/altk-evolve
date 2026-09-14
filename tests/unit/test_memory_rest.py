"""Host-injected REST authorization and client/namespace isolation."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.api.memory import MemoryScope, build_memory_router
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.frontend.services.context import injected_client
from altk_evolve.schema.core import Entity

pytestmark = pytest.mark.unit


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLVE_RETENTION_STORE_PATH", raising=False)
    backend = EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))
    for namespace in ("instance-a", "instance-b"):
        backend.ensure_namespace(namespace)
        backend.update_entities(
            namespace,
            [
                Entity(type="fact", content=f"{namespace}-{user}", metadata={"user_id": user, "secret": "do not expose"})
                for user in ("alice", "bob")
            ],
            False,
        )

    # Test-only identity provider. Production hosts must verify credentials here.
    def scope(x_user: str = Header("alice"), x_instance: str = Header("instance-a"), x_manage: str = Header("no")):
        return MemoryScope(namespace_id=x_instance, user_id=x_user, can_manage=x_manage == "yes")

    app = FastAPI()
    app.include_router(build_memory_router(client_dependency=lambda: backend, scope_dependency=scope), prefix="/api")
    return TestClient(app), backend


def test_personal_inventory_isolates_users_and_instances(app_client):
    http, _ = app_client
    for namespace in ("instance-a", "instance-b"):
        for user in ("alice", "bob"):
            response = http.get("/api/memory/entities", headers={"x-user": user, "x-instance": namespace})
            assert response.status_code == 200
            assert [item["content"] for item in response.json()["items"]] == [f"{namespace}-{user}"]
    assert injected_client.get() is None


def test_personal_crud_denies_other_user_and_identity_patch(app_client):
    http, backend = app_client
    entity = next(e for e in backend.scan_entities("instance-a") if e.metadata["user_id"] == "bob")
    for method, path, body in [
        ("GET", f"/api/memory/entities/{entity.id}", None),
        ("DELETE", f"/api/memory/entities/{entity.id}", None),
        ("PATCH", f"/api/memory/entities/{entity.id}/metadata", {"metadata": {"title": "stolen"}}),
    ]:
        response = http.request(method, path, json=body)
        assert response.status_code == 403
    own = next(e for e in backend.scan_entities("instance-a") if e.metadata["user_id"] == "alice")
    assert http.patch(f"/api/memory/entities/{own.id}/metadata", json={"metadata": {"user_id": "bob"}}).status_code == 400
    assert len(backend.scan_entities("instance-a")) == 2


def test_admin_access_is_explicit_and_content_free(app_client):
    http, backend = app_client
    assert http.get("/api/manage/memory/entities").status_code == 403
    response = http.get("/api/manage/memory/entities", headers={"x-manage": "yes"})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    for item in response.json()["items"]:
        assert "content" not in item and "content_preview" not in item
        assert "secret" not in item["metadata"]
    backend.update_entities("instance-a", [Entity(type="fact", content="only in a")], False)
    entity = backend.scan_entities("instance-a")[-1]
    assert http.get(f"/api/manage/memory/entities/{entity.id}", headers={"x-manage": "yes", "x-instance": "instance-b"}).status_code == 404


def test_anonymous_personal_access_rejected(app_client):
    http, _ = app_client
    assert http.get("/api/memory/entities", headers={"x-user": "default_user"}).status_code == 401
    assert http.get("/api/memory/facts", headers={"x-user": "default"}).status_code == 401


def test_schedule_rest_roundtrip_revision_and_namespace(app_client):
    http, _ = app_client
    headers = {"x-manage": "yes"}
    assert http.put("/api/manage/retention/policies/p", json={"name": "P", "policy": {"rules": []}}, headers=headers).status_code == 200
    payload = {
        "definition": {
            "policy_id": "p",
            "spec": {"schedule": "0 2 * * *", "timeZone": "America/Los_Angeles", "concurrencyPolicy": "Forbid"},
        }
    }
    assert http.put("/api/manage/retention/schedules/daily", json=payload).status_code == 403
    response = http.put("/api/manage/retention/schedules/daily", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["revision"] == 1
    assert http.put("/api/manage/retention/schedules/daily", json=payload, headers=headers).status_code == 409
    assert http.get("/api/manage/retention/schedules/daily", headers={**headers, "x-instance": "instance-b"}).status_code == 404
    assert http.get("/api/manage/retention/schedules", headers={**headers, "x-instance": "instance-b"}).json()["items"] == []
    assert len(http.get("/api/manage/retention/schedules/daily", headers=headers).json()["next_runs"]) == 5
    assert http.delete("/api/manage/retention/schedules/daily?expected_revision=1", headers=headers).json()["deleted"] is True


def test_request_cannot_supply_scope_or_actor(app_client):
    http, _ = app_client
    assert (
        http.post(
            "/api/manage/retention/runs",
            headers={"x-manage": "yes"},
            json={"policy_id": "p", "namespace_id": "instance-b", "initiated_by": "bob"},
        ).status_code
        == 422
    )


def test_concurrent_requests_keep_client_and_scope_local(app_client):
    http, _ = app_client

    def read(index):
        namespace = "instance-a" if index % 2 else "instance-b"
        response = http.get("/api/memory/entities", headers={"x-instance": namespace})
        return response.json()["items"][0]["content"] == f"{namespace}-alice"

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(read, range(12)))


def test_personal_facts_respect_injected_agent_scope(app_client):
    _, backend = app_client
    backend.update_entities(
        "instance-a",
        [
            Entity(type="fact", content="agent-a secret", metadata={"user_id": "alice", "agent_id": "a"}),
            Entity(type="fact", content="agent-b secret", metadata={"user_id": "alice", "agent_id": "b"}),
        ],
        False,
    )
    app = FastAPI()
    app.include_router(
        build_memory_router(
            client_dependency=lambda: backend,
            scope_dependency=lambda: MemoryScope(namespace_id="instance-a", user_id="alice", agent_id="a"),
        )
    )
    with TestClient(app) as http:
        result = http.get("/memory/facts").json()
        assert result["matched_count"] == 1
        assert result["categories"]["misc"][0]["content"] == "agent-a secret"
