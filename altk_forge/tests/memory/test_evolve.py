import pytest

from altk_forge.memory import evolve as E
from altk_forge.sources.http import SourceError


class FakeServer:
    """Stands in for sources.request against an altk-evolve server: routes (method, path) to handlers."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers=None, body=None, timeout=60, label=""):
        path = url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append((method, path, body, headers))
        handler = self.routes.get((method, path))
        if handler is None:
            raise SourceError(f"{method} {path} not found", 404)
        result = handler(body, headers) if callable(handler) else handler
        return 200, result, {}


@pytest.fixture
def server(monkeypatch):
    def install(routes):
        fake = FakeServer(routes)
        monkeypatch.setattr(E, "request", fake)
        return fake

    return install


def test_profile_definition_types_the_settings():
    ev = E.Evolve("http://ev", guidelines_mode="all", segmentation_enabled="true", trajectory_max_steps="20", custom_llm_provider="")
    config = ev.profile_definition()["processors"][0]["config"]
    assert config == {"guidelines_mode": "all", "segmentation_enabled": True, "trajectory_max_steps": 20}


def test_ensure_profile_creates_then_reuses(server):
    fake = server({("PUT", "api/processing-profiles/forge__p1"): {"revision": 1}})
    ev = E.Evolve("http://ev", "key", processing_profile="forge__p1", guidelines_mode="all")
    assert ev.ensure_profile() == 1
    assert fake.calls[-1][3]["If-None-Match"] == "*"
    assert fake.calls[-1][3]["Authorization"] == "Bearer key"

    current = {"revision": 3, "manifest": {"processors": [{"config": {"guidelines_mode": "all"}}]}}
    fake = server({("GET", "api/processing-profiles/forge__p1"): current})
    assert ev.ensure_profile() == 3
    assert [c[0] for c in fake.calls] == ["GET"]


def test_ensure_profile_updates_a_changed_profile_and_respects_a_pinned_revision(server):
    current = {"revision": 3, "manifest": {"processors": [{"config": {"guidelines_mode": "standard"}}]}}
    fake = server({("GET", "api/processing-profiles/forge"): current, ("PUT", "api/processing-profiles/forge"): {"revision": 4}})
    assert E.Evolve("http://ev", guidelines_mode="all").ensure_profile() == 4
    assert fake.calls[-1][3]["If-Match"] == '"3"'
    assert E.Evolve("http://ev", processing_profile="x", profile_revision="7").ensure_profile() == 7


def test_send_records_created_and_merged_entities(server):
    result = {
        "updates": [{"id": "5", "event": "ADD"}, {"id": "2", "event": "UPDATE"}, {"id": "3", "event": "NONE"}],
        "entities": [{"id": "5", "type": "guideline", "content": "Count with COUNT(*)"}, {"id": "10", "type": "fact"}],
        "operation_id": "op1",
    }
    fake = server(
        {
            ("POST", "api/namespaces"): {},
            ("GET", "openapi.json"): {"components": {"schemas": {"Trajectory": {"properties": {"messages": {}, "metadata": {}}}}}},
            ("POST", "api/trajectories"): result,
        }
    )
    known = set()
    piece = {"namespace_id": "ns", "trajectory": {"messages": [], "outcome": {"success": False}, "batch": {}}}
    r = E.Evolve("http://ev").send(piece, 2, known)
    assert r == {
        "namespace": "ns",
        "entity_ids": ["5", "10"],
        "updated_ids": ["2"],
        "guidelines": ["Count with COUNT(*)"],
        "operation_id": "op1",
    }
    assert known == {"ns"}
    body = fake.calls[-1][2]
    # Fields the server's Trajectory model does not know are dropped; the outcome goes to metadata.evidence.
    assert set(body["trajectory"]) == {"messages", "metadata"}
    assert body["trajectory"]["metadata"]["evidence"] == "failure"
    assert body["processing_profile"] == {"id": "forge", "revision": 2}


def test_send_drops_a_field_the_server_forbids(server):
    def trajectories(body, _):
        if "outcome" in body["trajectory"]:
            raise SourceError('422 {"type":"extra_forbidden","loc":["body","trajectory","outcome"]}', 422)
        return {"entities": []}

    server({("POST", "api/namespaces"): {}, ("POST", "api/trajectories"): trajectories})
    r = E.Evolve("http://ev").send({"namespace_id": "ns", "trajectory": {"messages": [], "outcome": {"success": True}}}, None, {"ns"})
    assert r["entity_ids"] == []


def test_ensure_namespace_tolerates_an_existing_one(server):
    def create(body, _):
        raise SourceError("exists", 400)

    server({("POST", "api/namespaces"): create})
    known = set()
    E.Evolve("http://ev").ensure_namespace("ns", known)
    assert known == {"ns"}


def test_delete_for_traces_also_deletes_merged_entities(server):
    deleted = []
    entities = [
        {"id": "1", "metadata": {}},
        {"id": "2", "metadata": {"source_task_id": "trace-1:llm-2"}},
        {"id": "3", "metadata": {"trace_id": "other"}},
    ]
    routes = {("GET", "api/namespaces/ns/entities?limit=500"): entities}
    for i in "123":
        routes[("DELETE", f"api/namespaces/ns/entities/{i}")] = lambda b, h, i=i: (deleted.append(i))
    server(routes)
    assert E.Evolve("http://ev").delete_for_traces("ns", {"1"}, ["trace-1"]) == 2
    assert deleted == ["1", "2"]
