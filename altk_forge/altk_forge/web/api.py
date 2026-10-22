"""The JSON API: which pipeline, converter or settings function a request (method and path) calls.

HTTP itself (static files, request bodies, the origin check, responses) is in web.server; the routes are listed in
its docstring."""

import json

from .. import converters as C
from .. import llm as S
from .. import pipelines as P
from ..core.errors import InputError
from ..core.paths import pipe_dir


def crud(method, rest, body, lister, saver, deleter):
    if not rest:
        return lister() if method == "GET" else saver(body()) if method == "POST" else None
    if len(rest) == 1 and method == "PUT":
        return saver(body(), rest[0])
    if len(rest) == 1 and method == "DELETE":
        deleter(rest[0])
        return {"deleted": rest[0]}
    return None


def api(method, parts, query, body):
    """The result of one API request: a JSON-able value, bytes (JSON already encoded), a string (plain text), or None
    (not found). `body` reads the request body; InputError and ConverterError are the client's mistakes (400)."""
    head, rest = parts[0], parts[1:]
    if head == "kinds" and method == "GET":
        return {
            "integrations": P.KINDS,
            "filters": P.FILTER_KINDS,
            "modes": P.MODES,
            "memory": {"fields": P.MEMORY_FIELDS, "choices": P.MEMORY_CHOICES},
        }
    if head == "folders" and not rest and method == "GET":
        return P.browse_folder(query.get("path"))
    if head == "settings":
        if not rest and method == "GET":
            return S.public_settings()
        if not rest and method == "PUT":
            return S.save_settings(body())
        if rest == ["test-llm"] and method == "POST":
            return S.test_llm(body().get("llm"))
        return None
    if head == "integrations":
        if rest[1:] == ["test"] and method == "POST":
            return P.test_integration(rest[0])
        return crud(method, rest, body, P.list_integrations, P.save_integration, P.delete_integration)
    if head == "filters":
        if len(rest) == 1 and method == "GET":
            return P.get_filter(rest[0])
        return crud(method, rest, body, P.list_filters, P.save_filter, P.delete_filter)
    if head == "preview" and method == "POST":
        b = body()
        return P.preview(b.get("integration_id"), b.get("filter_ids"), b.get("criteria"), b.get("kind") or "langfuse")
    if head == "pipelines":
        if not rest or (len(rest) == 1 and method == "PUT"):
            return crud(method, rest, body, P.list_pipelines, P.save_pipeline, None)
        return pipeline_api(method, rest[0], rest[1:], query, body)
    return None


def pipeline_api(method, pid, rest, query, body):
    if not rest:
        if method == "GET":
            return P.pipeline_view(pid)
        if method == "DELETE":
            P.delete_pipeline(pid, purge_memory=query.get("memory") == "1")
            return {"deleted": pid}
        return None
    action, more = rest[0], rest[1:]
    if method == "POST" and not more:
        if action == "run":
            return P.WORKER.run_now(pid)
        if action == "enable":
            return P.set_enabled(pid, body().get("enabled", True))
        if action == "promote":
            return P.promote(pid)
        if action == "rescan":
            P.reset_watermarks(pid)
            return {"ok": True}
        if action == "purge":
            b = body()
            return P.purge(pid, bool(b.get("memory", True)), bool(b.get("trajectories", True)), b.get("trace_ids"))
        if action == "retry":
            P.retry_send(pid, body().get("trace_id"))
            return {"ok": True}
        if action == "retry-convert":
            return P.retry_failed(pid, body().get("trace_id"))
        if action == "send-all":
            return P.send_all(pid)
        return None
    if action == "send-all" and more == ["stop"] and method == "POST":
        return P.stop_send_all(pid) or {}
    if action == "converters" and len(more) == 1 and method == "DELETE":
        if P.get_pipeline(pid)["mode"] == "production":
            raise InputError("A production pipeline keeps its converters")
        with P.RUNTIME.pipeline_lock(pid):
            C.delete_converter(pid, more[0])
        return {"deleted": more[0]}
    if action == "trajectories" and len(more) == 2 and method == "POST":
        tid, op = more
        if op == "send":
            if P.get_pipeline(pid)["mode"] == "production":
                raise InputError("A production pipeline sends on its own")
            return P.send_trace(pid, tid)
        if op == "unsend":
            if P.get_pipeline(pid)["mode"] == "production":
                raise InputError("A production pipeline keeps what it sent; switch it to test first")
            return P.purge(pid, memory=True, trajectories=False, trace_ids=[tid])
        return None
    if method != "GET":
        return None
    P.get_pipeline(pid)  # unknown pipeline -> 400 with its name
    root = pipe_dir(pid)
    if action == "log":
        return P.read_log(pid)
    if action == "send-all" and not more:
        return P.send_all_progress(pid) or {}
    if action == "failures" and not more:
        return [
            {"trace_id": tid, **{k: e.get(k) for k in ("name", "timestamp", "problems", "attempts", "processed_at")}}
            for tid, e in P.state_of(pid)["traces"].items()
            if e.get("status") == "failed"
        ]
    if action == "converters":
        if not more:
            return C.list_converters(pid)
        if len(more) == 1:
            return C.converter_detail(pid, more[0], query.get("version"))
        return None
    if action == "trajectories":
        if not more:
            index = root / "trajectories" / "index.json"
            return index.read_bytes() if index.exists() else []
        if len(more) == 1:
            doc = P.trajectory(pid, more[0])
            return None if doc is None else json.dumps(doc, ensure_ascii=False).encode()
        if more[1:] == ["slices"]:
            return P.slices_of(pid, more[0])
        if more[1:] == ["sending"]:
            return {"progress": P.send_progress(pid, more[0])}
        return None
    if action == "memory" and len(more) == 1:
        return P.memory_guidelines(pid, more[0])
    if action == "sessions":
        return [] if not more else None  # converted traces have no session documents yet
    return None
