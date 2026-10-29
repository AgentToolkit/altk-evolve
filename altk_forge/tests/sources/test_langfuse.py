from altk_forge.sources import langfuse


def test_langfuse_pages_through_traces(monkeypatch):
    calls = []

    def fake_request(method, url, headers=None, body=None, timeout=60, label=""):
        calls.append(url)
        page = int(url.split("page=")[1].split("&")[0])
        data = [{"id": f"t{page}", "timestamp": "2026-01-01T00:00:00Z"}]
        return 200, {"data": data, "meta": {"totalPages": 2}}, {}

    monkeypatch.setattr(langfuse, "request", fake_request)
    lf = langfuse.Langfuse("http://lf/", "pk", "sk")
    traces = list(lf.list_traces([{"type": "string", "column": "name", "operator": "=", "value": "x"}]))
    assert [t["id"] for t in traces] == ["t1", "t2"]
    assert traces[0]["updated_at"] == "2026-01-01T00:00:00Z"
    assert all(u.startswith("http://lf/api/public/traces?") for u in calls)
    assert "filter=" in calls[0]
    assert lf.auth == "Basic cGs6c2s="
