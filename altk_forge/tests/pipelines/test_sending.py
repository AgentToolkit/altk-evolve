import json

import pytest

from altk_forge.core.errors import InputError
from altk_forge.core.paths import pipe_dir
from altk_forge.core.store import write_json
from altk_forge.pipelines.manage import purge
from altk_forge.pipelines.sending import send_trace
from altk_forge.pipelines.state import counts, memory_of, unsent
from altk_forge.pipelines.trajectories import slices_of
from altk_forge.pipelines.worker import WORKER


def test_send_and_purge_in_test_mode(pipeline, chat, evolve):
    WORKER.run_once("qa-bot")
    tid = unsent("qa-bot")[0]
    view = slices_of("qa-bot", tid)
    assert view["slices"] == []  # the simple converter has no llm calls: nothing to slice
    record = send_trace("qa-bot", tid)
    assert record["sent_at"] and record["slices"] == []
    with pytest.raises(InputError, match="Already sent"):
        send_trace("qa-bot", tid)
    assert counts("qa-bot")["sent"] == 1
    deleted = purge("qa-bot", memory=True, trajectories=True)
    assert deleted["trajectories"] == 2
    assert counts("qa-bot")["processed"] == 0


def test_send_trace_records_each_slice(pipeline, evolve, trajectory):
    root = pipe_dir("qa-bot")
    (root / "trajectories").mkdir(parents=True)
    (root / "trajectories" / "trace-1.trajectory.json").write_text(json.dumps(trajectory))
    write_json(root / "state.json", {"traces": {"trace-1": {"status": "built", "timestamp": "t"}}, "watermarks": {}})
    record = send_trace("qa-bot", "trace-1")
    assert [(s["span_id"], s["namespace"], s["entity_ids"]) for s in record["slices"]] == [
        ("llm-1", "bot__sql_agent__tool_call", ["1"]),
        ("llm-2", "qa_bot__sql_agent__answer", ["2"]),
    ]
    assert [p["namespace_id"] for p in evolve.sent] == ["bot__sql_agent__tool_call", "qa_bot__sql_agent__answer"]
    shown = slices_of("qa-bot", "trace-1")["slices"]
    assert all(s["sent"] for s in shown)
    # Unsend deletes exactly what the trace produced, namespace by namespace.
    purge("qa-bot", memory=True, trajectories=False, trace_ids=["trace-1"])
    assert sorted(evolve.deleted) == [("bot__sql_agent__tool_call", ["1"], ["trace-1"]), ("qa_bot__sql_agent__answer", ["2"], ["trace-1"])]
    assert memory_of("qa-bot") == {}


def test_a_failed_send_is_recorded(pipeline, evolve, trajectory):
    evolve.fail = True
    root = pipe_dir("qa-bot")
    (root / "trajectories").mkdir(parents=True)
    (root / "trajectories" / "trace-1.trajectory.json").write_text(json.dumps(trajectory))
    with pytest.raises(InputError, match="altk-evolve is down"):
        send_trace("qa-bot", "trace-1")
    assert memory_of("qa-bot")["trace-1"]["error"] == "altk-evolve is down"
