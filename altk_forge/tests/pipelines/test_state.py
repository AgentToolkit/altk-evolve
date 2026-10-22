from altk_forge.core.paths import pipe_dir
from altk_forge.core.store import write_json
from altk_forge.pipelines.runtime import RUNTIME
from altk_forge.pipelines.state import counts, pipeline_integrations, reset_watermarks, state_of, unsent


def test_pipeline_integrations():
    assert pipeline_integrations({"inputs": [{"integration_id": "a"}, {"integration_id": "b"}], "output_id": "out"}) == {"a", "b", "out"}
    assert pipeline_integrations({"inputs": [], "output_id": None}) == set()


def test_a_new_pipeline_has_an_empty_state(pipeline):
    assert state_of("qa-bot") == {"traces": {}, "watermarks": {}}
    assert counts("qa-bot")["processed"] == 0


def test_unsent_lists_built_trajectories_not_in_memory_oldest_first(pipeline):
    root = pipe_dir("qa-bot")
    traces = {
        "new": {"status": "built", "timestamp": "2026-01-03"},
        "old": {"status": "built", "timestamp": "2026-01-01"},
        "failed": {"status": "failed", "timestamp": "2026-01-02"},
        "sent": {"status": "built", "timestamp": "2026-01-02"},
    }
    write_json(root / "state.json", {"traces": traces, "watermarks": {}})
    write_json(root / "memory.json", {"sent": {"sent_at": "2026-01-04", "slices": [{"entity_ids": ["1", "2"]}]}})
    assert unsent("qa-bot") == ["old", "new"]
    c = counts("qa-bot")
    assert (c["processed"], c["trajectories"], c["failed"], c["sent"], c["unsent"], c["entities"]) == (4, 3, 1, 1, 2, 2)


def test_reset_watermarks_wakes_the_worker(pipeline):
    root = pipe_dir("qa-bot")
    write_json(root / "state.json", {"traces": {"t": {"status": "built"}}, "watermarks": {"in": "2026-01-01"}})
    RUNTIME.schedule("qa-bot", 10**12)
    reset_watermarks("qa-bot")
    assert state_of("qa-bot") == {"traces": {"t": {"status": "built"}}, "watermarks": {}}
