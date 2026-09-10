"""Exercise MCP user-fact isolation against real filesystem persistence."""

import json

import pytest

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.frontend.mcp import mcp_server as server

pytestmark = pytest.mark.unit


@pytest.fixture
def client(tmp_path, monkeypatch):
    client = EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))
    monkeypatch.setattr(server, "get_client", lambda: client)
    monkeypatch.setattr(server, "_initialized_namespaces", set())
    monkeypatch.setattr(server.evolve_config, "namespace_id", "legacy")
    monkeypatch.setattr(server, "extract_facts_from_messages", lambda messages: [messages[0]["content"]])
    return client


def test_facts_are_isolated_by_namespace_and_user(client):
    scopes = [("instance-a", "alice"), ("instance-b", "alice"), ("instance-a", "bob"), ("instance-a", "default")]
    for namespace, user in scopes:
        response = json.loads(
            server.store_user_facts(
                user, f"private-{namespace}-{user}", namespace_id=namespace, metadata=json.dumps({"user_id": "impostor"})
            )
        )
        assert response["stored_count"] == 1
    for namespace, user in scopes:
        response = json.loads(server.retrieve_user_facts(user, namespace_id=namespace))
        assert response["matched_count"] == 1
        assert response["categories"]["misc"][0]["content"] == f"private-{namespace}-{user}"
    assert json.loads(server.retrieve_user_facts("unknown", namespace_id="instance-a"))["matched_count"] == 0
    assert json.loads(server.retrieve_user_facts("alice", namespace_id="missing"))["matched_count"] == 0
    assert not client.namespace_exists("missing")
    assert not client.namespace_exists("legacy")


def test_legacy_default_namespace_and_default_user_fallback(client):
    server.store_user_facts("default", "shared legacy preference")
    result = json.loads(server.retrieve_user_facts("alice"))
    assert result["categories"]["misc"][0]["content"] == "shared legacy preference"


@pytest.mark.parametrize("user", ["", " "])
def test_scoped_facts_require_nonblank_user(client, user):
    result = json.loads(server.store_user_facts(user, "private", namespace_id="instance-a"))
    assert "error" in result
    result = json.loads(server.retrieve_user_facts(user, namespace_id="instance-a"))
    assert "error" in result
    assert not client.namespace_exists("instance-a")


@pytest.mark.parametrize("namespace", ["", " "])
def test_explicit_blank_namespace_does_not_use_default(client, namespace):
    assert "error" in json.loads(server.store_user_facts("alice", "private", namespace_id=namespace))
    assert "error" in json.loads(server.retrieve_user_facts("alice", namespace_id=namespace))
    assert not client.namespace_exists("legacy")


def test_mcp_transport_accepts_namespace_and_enforces_user_scope(client):
    import asyncio
    from fastmcp import Client

    async def exercise():
        async with Client(server.mcp) as mcp:
            await mcp.call_tool_mcp("store_user_facts", {"namespace_id": "instance-a", "user_id": "alice", "message": "private"})
            for namespace, user, count in [("instance-a", "alice", 1), ("instance-b", "alice", 0), ("instance-a", "bob", 0)]:
                result = await mcp.call_tool_mcp("retrieve_user_facts", {"namespace_id": namespace, "user_id": user})
                assert not result.isError
                assert json.loads(result.content[0].text)["matched_count"] == count

    asyncio.run(exercise())
