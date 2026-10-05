"""The CUGA adapter, on hand-written synthetic run directories under fixtures/cuga."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.llm.guidelines.guidelines import parse_openai_agents_trajectory
from altk_evolve.processing import ProcessingManager, TrajectoryOutcome

from experiments.guideline_pipeline import __main__ as cli
from experiments.guideline_pipeline.adapters import ADAPTERS, AdapterRecord, get_adapter
from experiments.guideline_pipeline.adapters.cuga import PARSER_STEP_LIMIT, CugaAdapter, elide_middle
from experiments.guideline_pipeline.stages.mine import mine

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "cuga"
DEV = FIXTURES / "test_normal_dev"
PROFILE = Path(__file__).parents[1] / "profiles" / "cuga.json"


def by_id(adapter: CugaAdapter, path: Path = FIXTURES) -> dict[str, AdapterRecord]:
    return {record.trace_id: record for record in adapter.records(path)}


@pytest.fixture
def run_copy(tmp_path: Path) -> Path:
    return Path(shutil.copytree(DEV, tmp_path / "test_normal_dev"))


def test_registered_and_discovers_tasks_by_manifest_or_glob(tmp_path: Path):
    assert isinstance(ADAPTERS["cuga"], CugaAdapter)
    # The parent holds two runs: dev has a manifest (so zzz9999_1 is skipped), train is globbed.
    assert list(by_id(CugaAdapter())) == ["aaa0001_1", "aaa0001_2", "ccc0003_1"]
    assert list(by_id(CugaAdapter(), DEV)) == ["aaa0001_1", "aaa0001_2"]
    with pytest.raises(ValueError, match="No CUGA run directory"):
        next(CugaAdapter().records(tmp_path))


def test_messages_are_rebuilt_in_the_extractor_shape():
    record = by_id(CugaAdapter())["aaa0001_1"]

    assert record.messages[0] == {"role": "user", "content": "List my open todos."}
    call = record.messages[1]["content"][0]
    assert call["type"] == "function_call" and call["id"] == "call_0"
    assert call["function"] == {
        "name": "execute_ipython",
        "arguments": json.dumps({"code": "todos = todo_list(status='open')\nprint(todos)"}),
    }
    assert record.messages[2] == {"role": "assistant", "content": "OBSERVATION:\n[{'id': 1, 'text': 'buy milk'}]"}
    assert record.messages[3]["content"][0]["id"] == "call_1"
    # Duplicate logs (Assistant_code, Assistant_nl), the final answer, the evaluation and
    # the summary are not steps; the outcome is a field, not an injected message.
    assert len(record.messages) == 4
    assert "EvaluationResult" not in json.dumps(record.messages) and "Summary" not in json.dumps(record.messages)
    assert record.context_messages == []

    parsed = parse_openai_agents_trajectory(record.messages)
    assert parsed["task_instruction"] == "List my open todos."
    assert parsed["num_steps"] == 3
    assert parsed["steps_list"][0].startswith("**Step 1 - Action:**\nexecute_ipython(code=")


def test_outcome_mapping():
    records = by_id(CugaAdapter())

    success = records["aaa0001_1"].outcome
    assert success is not None and success.success and success.failed_checks == ()
    assert success.detail is not None and "PASSED checks:\n  - assert answers match." in success.detail

    failure = records["aaa0001_2"].outcome
    assert failure is not None and not failure.success
    assert failure.failed_checks == ("assert todo 'buy milk' is marked done",)
    assert failure.detail is not None
    assert failure.detail.startswith("num_tests=2 pass_count=1 pass_percentage=50.0")
    assert "FAILED checks:\n  - assert todo 'buy milk' is marked done\n    trace: AssertionError: False == True" in failure.detail

    # No results entry, no EvaluationResult step and no score: no outcome, not a guessed failure.
    assert records["ccc0003_1"].outcome is None


def test_outcome_fallbacks_and_detail_cap():
    adapter = CugaAdapter()
    by_score = adapter.record("run", "t_1", {"intent": "x", "score": 0.0, "steps": []}, None)
    assert by_score.outcome == TrajectoryOutcome(success=False)

    huge = {"success": False, "evaluation": {"failures": [{"requirement": "r", "trace": "x" * 5000}] * 10}}
    from_results = adapter.record("run", "t_1", {"intent": "x", "steps": []}, {"eval": json.dumps(huge)})
    assert from_results.outcome is not None and from_results.outcome.failed_checks == ("r",)
    assert from_results.outcome.detail is not None and len(from_results.outcome.detail) == 2000

    # results.json is authoritative over the trajectory's own EvaluationResult step.
    step = {"name": "EvaluationResult", "data": json.dumps({"success": False})}
    overridden = adapter.record("run", "t_1", {"intent": "x", "steps": [step]}, {"eval": {"success": True}})
    assert overridden.outcome is not None and overridden.outcome.success


def test_malformed_evaluation_reports_are_skipped_not_fatal():
    adapter = CugaAdapter()
    mixed = {"success": False, "evaluation": {"passes": ["ok", {"requirement": "p"}], "failures": ["bad", None, {"requirement": "r"}]}}
    record = adapter.record("run", "t_1", {"intent": "x", "steps": []}, {"eval": mixed})
    assert record.outcome is not None and record.outcome.failed_checks == ("r",)
    assert record.outcome.detail is not None and "  - p" in record.outcome.detail and "bad" not in record.outcome.detail

    for report in (["not", "a", "dict"], "text", {"failures": "text", "passes": {"requirement": "p"}}):
        outcome = adapter.record("run", "t_1", {"intent": "x", "steps": []}, {"eval": {"success": False, "evaluation": report}}).outcome
        assert outcome is not None and not outcome.success and outcome.failed_checks == ()


def test_identity_metadata_and_model():
    records = by_id(CugaAdapter(model="gpt-4.1"))
    record = records["aaa0001_2"]

    assert (record.batch.source, record.batch.conversation_id, record.batch.batch_id) == ("cuga", "test_normal_dev/aaa0001_2", "aaa0001_2")
    assert record.model == "gpt-4.1"
    assert record.metadata == {
        "run": "test_normal_dev",
        "partition": "test_normal_dev",
        "dataset": "synthetic",
        "experiment": "synthetic_dev",
        "model": "gpt-4.1",
        "score": 0.5,
        "pass_percentage": 50.0,
    }
    assert records["ccc0003_1"].metadata == {"run": "test_normal_train", "partition": "test_normal_train", "model": "gpt-4.1"}
    assert CugaAdapter().record("cuga_all_dev_gpt", "t", {"steps": []}, None).metadata["partition"] == "dev"


def test_batch_is_stable_and_the_revision_follows_content(run_copy: Path):
    first = by_id(CugaAdapter(), run_copy)
    assert by_id(CugaAdapter(), run_copy) == first

    task = json.loads((run_copy / "aaa0001_2.json").read_text())
    task["steps"][3]["data"] = "I updated the todo after all."
    (run_copy / "aaa0001_2.json").write_text(json.dumps(task))
    edited = by_id(CugaAdapter(), run_copy)
    assert edited["aaa0001_1"].batch == first["aaa0001_1"].batch
    assert edited["aaa0001_2"].batch.batch_id == first["aaa0001_2"].batch.batch_id
    assert edited["aaa0001_2"].batch.revision != first["aaa0001_2"].batch.revision

    results = json.loads((run_copy / "results.json").read_text())
    results["aaa0001_1"]["eval"] = json.dumps({"success": False})
    (run_copy / "results.json").write_text(json.dumps(results))
    regraded = by_id(CugaAdapter(), run_copy)
    assert regraded["aaa0001_1"].batch.revision != first["aaa0001_1"].batch.revision

    # Opting into context changes what processors see, so it is a new revision too.
    with_context = by_id(CugaAdapter(include_summaries=True), run_copy)
    assert with_context["aaa0001_2"].batch.revision != edited["aaa0001_2"].batch.revision


def test_long_trajectories_keep_head_and_tail():
    steps = [{"role": "assistant", "content": f"OBSERVATION:\nstep {i}"} for i in range(80)]
    elided = elide_middle(steps)

    assert len(elided) == PARSER_STEP_LIMIT
    assert elided[:6] == steps[:6] and elided[7:] == steps[-43:]
    assert elided[6] == {"role": "assistant", "content": "[31 steps elided: the trajectory continues below]"}
    assert elide_middle(steps[:50]) == steps[:50]

    task = {"intent": "long task", "steps": [{"name": "Raw_Assistant_Response", "data": f"print({i})"} for i in range(70)]}
    record = CugaAdapter().record("run", "long_1", task, None)
    assert record.messages[0] == {"role": "user", "content": "long task"}
    parsed = parse_openai_agents_trajectory(record.messages)
    # Every step reaches the parser, and the last action survives the 50-step cap.
    assert parsed["num_steps"] == PARSER_STEP_LIMIT
    assert parsed["steps_list"][6] == "**Step 7 - Reasoning:**\n[21 steps elided: the trajectory continues below]"
    assert "print(69)" in parsed["steps_list"][-1]
    record.model_dump_json()


def test_context_is_opt_in():
    plain = by_id(CugaAdapter(), DEV)["aaa0001_2"]
    full = by_id(CugaAdapter(include_system_prompt=True, include_summaries=True), DEV)["aaa0001_2"]

    assert plain.context_messages == []
    assert full.messages == plain.messages
    assert full.context_messages == [
        {"role": "system", "content": "# ROLE\nYou are a synthetic test agent. Use the provided tools."},
        {"role": "assistant", "content": "SUMMARY after action 1:\nSummary of Progress:\nThe update tool name was wrong."},
    ]
    # Context carries no user message, so the parser still takes the task from messages.
    assert parse_openai_agents_trajectory(full.messages, context_messages=full.context_messages)["task_instruction"] == (
        "Mark the todo 'buy milk' as done."
    )
    only_prompt = by_id(CugaAdapter(include_system_prompt=True), DEV)["aaa0001_2"]
    assert [m["role"] for m in only_prompt.context_messages] == ["system"]


def test_options_and_task_selection(tmp_path: Path):
    manifest = tmp_path / "tasks.txt"
    manifest.write_text("# selected\naaa0001_2\n")
    adapter = get_adapter("cuga", {"include_summaries": "true", "task_manifest": str(manifest), "task_ids": "ccc0003_1", "model": "m"})

    assert isinstance(adapter, CugaAdapter) and adapter is not ADAPTERS["cuga"]
    assert (adapter.include_summaries, adapter.include_system_prompt, adapter.model) == (True, False, "m")
    assert list(by_id(adapter)) == ["aaa0001_2", "ccc0003_1"]
    json_manifest = tmp_path / "tasks.json"
    json_manifest.write_text(json.dumps({"tasks": [{"task_id": "aaa0001_1"}]}))
    assert list(by_id(CugaAdapter().configure({"task_manifest": str(json_manifest)}))) == ["aaa0001_1"]

    with pytest.raises(ValueError, match="Task IDs not found"):
        next(CugaAdapter(task_ids=frozenset({"nope_1"})).records(FIXTURES))
    with pytest.raises(ValueError, match="Unknown cuga option 'rubric_mode'"):
        CugaAdapter().configure({"rubric_mode": "review"})
    with pytest.raises(ValueError, match="must be true or false"):
        CugaAdapter().configure({"include_summaries": "maybe"})


def test_records_stream_one_task_at_a_time(run_copy: Path):
    (run_copy / "aaa0001_2.json").write_text("{ truncated")
    records = CugaAdapter().records(run_copy)

    assert isinstance(records, Iterator)
    assert next(records).trace_id == "aaa0001_1"  # the broken second file is not read yet
    with pytest.raises(json.JSONDecodeError):
        next(records)


def test_sample_profile_validates_with_the_builtin_processor():
    plan = ProcessingManager().validate(json.loads(PROFILE.read_text()))
    (processor,) = plan.manifest()["processors"]
    assert processor["plugin"] == "evolve.guidelines"
    assert (processor["config"]["guidelines_mode"], processor["config"]["segmentation_enabled"]) == ("standard", False)


def test_mine_dry_run_from_the_cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def no_client() -> EvolveClient:
        raise AssertionError("dry run must not touch storage")

    monkeypatch.setattr(cli, "make_client", no_client)
    argv = ["mine", "--adapter", "cuga", "--input", str(FIXTURES), "--namespace", "x", "--processing-profile", "x", "--dry-run"]

    assert cli.main(argv) == 0
    assert capsys.readouterr().out.strip() == "dry run: 3 valid, 0 failed"
    assert cli.main([*argv, "--adapter-option", "include_summaries=yes", "--adapter-option", "task_ids=aaa0001_1"]) == 0
    assert capsys.readouterr().out.strip() == "dry run: 1 valid, 0 failed"
    assert cli.main([*argv, "--adapter-option", "nope=1"]) == 2
    assert "Unknown cuga option 'nope'" in capsys.readouterr().err
    assert cli.main([*argv, "--adapter-option", "model=a", "--adapter-option", "model=b"]) == 2
    assert "given twice" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main([*argv, "--adapter-option", "novalue"])


def test_mine_checkpoints_each_task_and_stamps_evidence(client: EvolveClient, run_copy: Path):
    def run():
        return mine(CugaAdapter().records(run_copy), client, namespace_id="memories", processing_profile="echo")

    first, again = run(), run()
    assert (first.processed, first.skipped, again.processed, again.skipped) == (2, 0, 0, 2)
    stored = {e.metadata["processing"]["source_batch"]["batch_id"]: e for e in client.get_all_entities("memories")}
    assert {batch: e.metadata["success"] for batch, e in stored.items()} == {"aaa0001_1": True, "aaa0001_2": False}

    results = json.loads((run_copy / "results.json").read_text())
    results["aaa0001_2"]["eval"] = json.dumps({"success": True})
    (run_copy / "results.json").write_text(json.dumps(results))
    regraded = run()
    assert (regraded.processed, regraded.skipped) == (1, 1)
