import json

import pytest

from altk_forge.converters import writer
from altk_forge.core.errors import InputError
from altk_forge.core.paths import pipe_dir
from altk_forge.llm import LLMError
from altk_forge.pipelines import worker
from altk_forge.pipelines.log import read_log
from altk_forge.pipelines.manage import promote, purge, save_pipeline, set_enabled
from altk_forge.pipelines.state import counts, state_of
from altk_forge.pipelines.worker import WORKER, retry_failed
from forge_helpers import answer_with


def test_the_worker_converts_new_traces_once(pipeline, chat):
    run = WORKER.run_once("qa-bot")
    assert run["status"] == "ok", run
    assert (run["found"], run["new"], run["built"], run["failed"]) == (2, 2, 2, 0)
    assert len(chat) == 1  # the second trace has the same shape: the converter is reused
    c = counts("qa-bot")
    assert (c["trajectories"], c["unsent"], c["sent"], c["converters"]) == (2, 2, 0, 1)
    index = json.loads((pipe_dir("qa-bot") / "trajectories" / "index.json").read_text())
    assert sorted(row["question"] for row in index) == ["Q1?", "Q2?"]
    # The next poll finds nothing new.
    run = WORKER.run_once("qa-bot")
    assert (run["found"], run["new"]) == (2, 0)
    assert "Built" in read_log("qa-bot")


def test_traces_wait_until_they_settle(pipeline, chat):
    save_pipeline({**pipeline, "settle_seconds": 10**9}, "qa-bot")
    run = WORKER.run_once("qa-bot")
    assert (run["waiting"], run["new"]) == (2, 0)
    assert chat == []


def test_a_failed_trace_is_recorded_and_retried_on_request(pipeline, monkeypatch):
    monkeypatch.setattr(writer, "chat", lambda messages: answer_with("def convert(t, c): return {}"))
    run = WORKER.run_once("qa-bot")
    assert run["failed"] == 2 and run["built"] == 0
    failed = [e for e in state_of("qa-bot")["traces"].values() if e["status"] == "failed"]
    assert len(failed) == 2 and all("no working converter" in e["problems"][0] for e in failed)
    assert retry_failed("qa-bot") == {"queued": 2}


def test_an_unavailable_llm_stops_the_run_and_keeps_the_trace(pipeline, monkeypatch):
    def down(messages):
        raise LLMError("no model")

    monkeypatch.setattr(writer, "chat", down)
    run = WORKER.run_once("qa-bot")
    assert run["status"] == "failed" and "Converter LLM: no model" in run["message"]
    assert state_of("qa-bot")["traces"] == {}  # not processed: the next run tries again


def test_production_sends_new_trajectories_automatically(pipeline, chat, evolve, monkeypatch):
    promote("qa-bot")
    sent = []
    monkeypatch.setattr(worker, "send_trace", lambda pid, tid, *a, **k: sent.append(tid))
    run = WORKER.run_once("qa-bot")
    assert run["built"] == 2 and run["sent"] == 2
    assert sorted(sent) == sorted(state_of("qa-bot")["traces"])
    with pytest.raises(InputError, match="keeps what it produced"):
        purge("qa-bot", trajectories=True)


def test_a_paused_pipeline_stops_its_run(pipeline, chat):
    set_enabled("qa-bot", False)
    run = WORKER.run_once("qa-bot")
    assert run["status"] == "stopped"
    set_enabled("qa-bot", True)
    assert WORKER.run_once("qa-bot")["built"] == 2


def test_a_trajectory_shows_in_the_console_and_viewer_as_soon_as_it_is_built(pipeline, traces, monkeypatch):
    # A run over many new traces can take hours (an LLM call per new shape): the viewer's list, the console's counts
    # and its Open viewer button must not wait for the run to end. The second trace has another shape, so the LLM is
    # asked while the run is still going; that is when the first trajectory must already show.
    import json as _json

    from altk_forge.converters import writer
    from altk_forge.core.paths import pipe_dir
    from forge_helpers import SIMPLE_CONVERTER, answer_with

    (traces / "run2.json").write_text(_json.dumps({"question": "Q2?", "answer": 2}))
    seen = []

    def llm(messages):
        index = pipe_dir("qa-bot") / "trajectories" / "index.json"
        listed = [row["question"] for row in _json.loads(index.read_text())] if index.exists() else None
        seen.append((listed, counts("qa-bot")["trajectories"]))
        return answer_with(SIMPLE_CONVERTER)

    monkeypatch.setattr(writer, "chat", llm)
    run = WORKER.run_once("qa-bot")
    assert run["built"] == 2
    # (viewer list, console count) while each converter was being written: nothing before the first, then the first.
    assert seen == [(None, 0), (["Q1?"], 1)]
