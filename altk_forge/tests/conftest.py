"""Shared fixtures. Everything Forge stores goes to a temporary data folder, so no test touches .data/."""

import json
import os
import tempfile

# Before Forge is imported: env.DATA is read once, at import.
os.environ["FORGE_DATA"] = tempfile.mkdtemp(prefix="forge-test-")

import pytest  # noqa: E402

from altk_forge import llm  # noqa: E402
from altk_forge.converters import writer  # noqa: E402
from altk_forge.core import paths  # noqa: E402
from altk_forge.pipelines import (  # noqa: E402
    filters,
    integrations,
    manage,
    migrations,
    sending,
    state,
    worker,
)
from altk_forge.pipelines.integrations import (  # noqa: E402
    client_for,
    get_integration,
    save_integration,
)
from altk_forge.pipelines.manage import save_pipeline  # noqa: E402
from altk_forge.sources.http import SourceError  # noqa: E402
from forge_helpers import SIMPLE_CONVERTER, answer_with  # noqa: E402


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """An empty data folder for this test only."""
    # Modules keep the paths they import (from ... import PIPES), so each copy points at this folder.
    for module in (paths, migrations):
        monkeypatch.setattr(module, "DATA", tmp_path)
    for module in (paths, state, manage, migrations):
        monkeypatch.setattr(module, "PIPES", tmp_path / "pipelines")
    for module in (integrations, migrations):
        monkeypatch.setattr(module, "INTEGRATIONS", tmp_path / "integrations.json")
    monkeypatch.setattr(filters, "FILTERS", tmp_path / "filters.json")
    monkeypatch.setattr(llm, "SETTINGS", tmp_path / "settings.json")
    # A developer's .env must not leak into the tests.
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(integrations, "load_env", lambda *a, **k: None)
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def ts(second):
    return f"2026-01-01T00:00:{second:02d}.000Z"


SYSTEM_PROMPT = "You write SQL.\n\n# Guidelines from memory\n- Use COUNT(*) to count rows\n- Quote identifiers\n"
TOOL_CALL = {"id": "call1", "name": "run_sql", "args": {"q": "select count(*) from users"}}


@pytest.fixture
def langfuse_trace():
    """A Langfuse export of a one-agent LangGraph run: a tool call, the tool, and the answer."""
    return {
        "id": "trace-1",
        "name": "sql_bot:run",
        "userId": "u1",
        "projectId": "proj",
        "htmlPath": "/project/proj/traces/trace-1",
        "timestamp": ts(0),
        "environment": "default",
        "tags": ["test"],
        "input": {"messages": [{"type": "human", "content": "How many users?"}]},
        "output": {
            "messages": [{"type": "human", "content": "How many users?"}, {"type": "ai", "name": "sql_agent", "content": "42 users"}]
        },
        "metadata": {"expected_output": "42 users", "task_name": "count_users", "expected_tool_calls": ["run_sql"]},
        "scores": [{"name": "correctness", "value": 100, "dataType": "NUMERIC", "source": "API", "comment": "right"}],
        "observations": [
            {
                "id": "graph",
                "type": "CHAIN",
                "name": "LangGraph",
                "parentObservationId": None,
                "startTime": ts(0),
                "endTime": ts(10),
                "metadata": {"ls_integration": "langgraph"},
            },
            {
                "id": "agent-1",
                "type": "CHAIN",
                "name": "sql_agent",
                "parentObservationId": "graph",
                "startTime": ts(1),
                "endTime": ts(9),
                "metadata": {"langgraph_node": "sql_agent"},
                "output": {"update": {"messages": [{"type": "ai", "content": "42 users"}]}, "goto": "__end__"},
            },
            {
                "id": "llm-1",
                "type": "GENERATION",
                "name": "ChatOpenAI",
                "parentObservationId": "agent-1",
                "startTime": ts(2),
                "endTime": ts(3),
                "model": "gpt-4o",
                "input": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "How many users?"},
                    {"role": "tool", "content": {"type": "function", "function": {"name": "run_sql", "parameters": {"type": "object"}}}},
                ],
                "output": {"content": "", "tool_calls": [TOOL_CALL]},
                "usage": {"input": 10, "output": 5, "total": 15},
                "metadata": {
                    "ls_provider": "openai",
                    "memory_namespace": "bot__sql_agent__tool_call",
                    "memory_step": "sql_agent.tool_call",
                    "memory_query": "count users",
                    "memory_guideline_ids": [7, 8],
                    "memory_enabled": True,
                },
            },
            {
                "id": "tool-1",
                "type": "TOOL",
                "name": "run_sql",
                "parentObservationId": "agent-1",
                "startTime": ts(4),
                "endTime": ts(5),
                "input": {"q": "select count(*) from users"},
                "output": '[{"n": 42}]',
                "metadata": {"tool_call_id": "call1"},
            },
            {
                "id": "llm-2",
                "type": "GENERATION",
                "name": "ChatOpenAI",
                "parentObservationId": "agent-1",
                "startTime": ts(6),
                "endTime": ts(7),
                "model": "gpt-4o",
                "input": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "How many users?"},
                    {"role": "assistant", "content": "", "tool_calls": [TOOL_CALL]},
                    {"role": "tool", "content": '[{"n": 42}]', "tool_call_id": "call1"},
                ],
                "output": {"content": "42 users"},
                "usage": {"input": 30, "output": 3, "total": 33},
                "metadata": {"ls_provider": "openai"},
            },
        ],
    }


@pytest.fixture
def trajectory(langfuse_trace):
    """The trajectory the built-in converter makes of langfuse_trace."""
    from altk_forge.builtin.langfuse import convert

    return convert(langfuse_trace, "http://langfuse.test")


# ---------------------------------------------------------------- a pipeline: a folder of two traces in, a fake altk-evolve out


class FakeEvolve:
    """What pipelines need of evolve.Evolve, recording what is sent and deleted."""

    def __init__(self, fail=False):
        self.sent, self.deleted, self.fail = [], [], fail

    def ensure_profile(self):
        return 1

    def send(self, piece, revision, known):
        if self.fail:
            raise SourceError("altk-evolve is down")
        n = len(self.sent) + 1
        self.sent.append(piece)
        return {
            "namespace": piece["namespace_id"],
            "entity_ids": [str(n)],
            "updated_ids": [],
            "guidelines": [f"g{n}"],
            "operation_id": None,
        }

    def delete_for_traces(self, ns, ids, trace_ids):
        self.deleted.append((ns, sorted(ids), sorted(trace_ids)))
        return len(ids)

    def guidelines(self, ns):
        return [{"id": "1", "type": "guideline", "content": "g1"}]


@pytest.fixture
def evolve(monkeypatch):
    fake = FakeEvolve()
    real = client_for

    def client(iid, p=None):
        return fake if get_integration(iid)["kind"] == "altk_evolve" else real(iid, p)

    # Every module that sends to or deletes from memory looks the client up itself.
    for module in (sending, manage, worker):
        monkeypatch.setattr(module, "client_for", client)
    return fake


@pytest.fixture
def chat(monkeypatch):
    calls = []

    def stub(messages):
        calls.append(messages)
        return answer_with(SIMPLE_CONVERTER)

    monkeypatch.setattr(writer, "chat", stub)
    return calls


@pytest.fixture
def traces(tmp_path):
    folder = tmp_path / "traces"
    folder.mkdir()
    for i in (1, 2):
        f = folder / f"run{i}.json"
        f.write_text(json.dumps({"question": f"Q{i}?", "answer": f"A{i}"}))
        os.utime(f, (1_700_000_000 + i, 1_700_000_000 + i))  # settled long ago
    (folder / "summary.json").write_text("{}")
    return folder


@pytest.fixture
def pipeline(data_dir, traces):
    src = save_integration({"kind": "filesystem", "name": "Runs", "path": str(traces), "exclude": "summary.json"})
    key = "secret-key"  # pragma: allowlist secret
    out = save_integration({"kind": "altk_evolve", "name": "Memory", "host": "http://ev", "api_key": key})
    return save_pipeline({"name": "QA bot", "inputs": [{"integration_id": src["id"]}], "output_id": out["id"], "settle_seconds": 0})
