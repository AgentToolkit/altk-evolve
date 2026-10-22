import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from altk_forge.web import server


@pytest.fixture
def api(data_dir):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    call_origin = f"http://127.0.0.1:{port}"

    def call(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(method, path, json.dumps(body) if body is not None else None, headers or {})
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        is_json = r.getheader("Content-Type", "").startswith("application/json")
        return r.status, json.loads(raw) if is_json else raw.decode()

    call.origin = call_origin
    yield call
    httpd.shutdown()
    httpd.server_close()


def test_pages_are_served_and_other_files_are_not(api):
    status, body = api("GET", "/")
    assert status == 200 and "<html" in body.lower()
    assert api("GET", "/viewer.html")[0] == 200
    assert api("GET", "/server.py")[0] == 404


def test_kinds(api):
    status, body = api("GET", "/api/kinds")
    assert status == 200
    assert set(body["integrations"]) == {"langfuse", "filesystem", "phoenix", "altk_evolve"}
    assert body["modes"] == ["test", "production"]


def test_integration_crud_masks_secrets(api):
    status, made = api(
        "POST",
        "/api/integrations",
        {"kind": "altk_evolve", "name": "Memory", "host": "http://ev", "api_key": "secret-1234"},  # pragma: allowlist secret
    )
    assert status == 200 and made["api_key_hint"] == "…1234" and "api_key" not in made
    status, listed = api("GET", "/api/integrations")
    assert [i["id"] for i in listed] == ["memory"]
    assert "secret-1234" not in json.dumps(listed)
    assert api("DELETE", "/api/integrations/memory") == (200, {"deleted": "memory"})


def test_input_errors_are_400_with_the_message(api):
    status, body = api("POST", "/api/integrations", {"kind": "nope"})
    assert status == 400 and "Unknown kind" in body["error"]
    status, body = api("GET", "/api/pipelines/missing")
    assert status == 400 and "No pipeline missing" in body["error"]
    assert api("GET", "/api/unknown")[0] == 404


def test_cross_origin_writes_are_refused(api):
    status, body = api("POST", "/api/filters", {"name": "x"}, {"Origin": "http://evil.example"})
    assert status == 403 and "Cross-origin" in body["error"]
    # Reads are allowed, and so are writes from the server's own pages.
    assert api("GET", "/api/filters", headers={"Origin": "http://evil.example"})[0] == 200
    status, body = api("POST", "/api/filters", {"name": "Mine"}, {"Origin": api.origin})
    assert status == 200 and body["id"] == "mine"


def test_a_body_that_is_not_a_json_object(api):
    status, body = api("POST", "/api/filters", ["not", "an", "object"])
    assert status == 400 and "JSON object" in body["error"]


def test_pipeline_lifecycle_over_http(api, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    _, src = api("POST", "/api/integrations", {"kind": "filesystem", "name": "In", "path": str(folder)})
    status, p = api("POST", "/api/pipelines", {"name": "Demo", "inputs": [{"integration_id": src["id"]}]})
    assert status == 200 and p["id"] == "demo" and p["counts"]["processed"] == 0
    assert api("GET", "/api/pipelines/demo/trajectories") == (200, [])
    assert api("GET", "/api/pipelines/demo/converters") == (200, [])
    status, body = api("POST", "/api/pipelines/demo/promote")
    assert status == 400 and "output" in body["error"]
    status, body = api("POST", "/api/pipelines/demo/enable", {"enabled": False})
    assert status == 200 and body["enabled"] is False
    assert api("DELETE", "/api/pipelines/demo") == (200, {"deleted": "demo"})


def test_settings_over_http(api):
    key = "sk-9999"  # pragma: allowlist secret
    status, body = api("PUT", "/api/settings", {"llm": {"base_url": "http://llm", "model": "m", "api_key": key}})
    assert status == 200 and body["llm"]["api_key_hint"] == "…9999"
    status, body = api("GET", "/api/settings")
    assert body["llm"]["model"] == "m" and "api_key" not in body["llm"]


def test_the_original_trace_comes_back_exactly_as_it_was_imported(data_dir, pipeline, chat, traces):
    from altk_forge.pipelines.worker import WORKER
    from altk_forge.sources.filesystem import Filesystem

    exact = '{"answer":"A1",  "question": "Q1?"}'
    (traces / "run1.json").write_text(exact)
    WORKER.run_once("qa-bot")
    tid = Filesystem.trace_id("run1.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request("GET", f"/api/pipelines/qa-bot/trajectories/{tid}/trace")
        r = conn.getresponse()
        assert r.status == 200 and r.getheader("Content-Type").startswith("application/json")
        assert r.read() == exact.encode()
        conn.request("GET", "/api/pipelines/qa-bot/trajectories/unknown/trace")
        r = conn.getresponse()
        r.read()
        assert r.status == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
