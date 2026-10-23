"""The CUGA adapter, on hand-written synthetic run directories under fixtures/cuga."""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.llm.guidelines.guidelines import parse_openai_agents_trajectory
from altk_evolve.processing import ProcessingManager, TrajectoryOutcome

from experiments.guideline_pipeline import __main__ as cli
from experiments.guideline_pipeline.adapters import ADAPTERS, AdapterRecord, get_adapter
from experiments.guideline_pipeline.adapters.cuga import _AGENT_VIEW_STEP_LIMIT, CugaAdapter
from experiments.guideline_pipeline.stages.mine import mine
from experiments.guideline_pipeline.tests.fakes import profile_window

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


def test_a_stray_json_beside_runs_is_ignored_not_read_as_the_run(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    runs = Path(shutil.copytree(FIXTURES, tmp_path / "runs"))
    (runs / "summary.json").write_text(json.dumps({"total": 3}))

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        assert list(by_id(CugaAdapter(), runs)) == ["aaa0001_1", "aaa0001_2", "ccc0003_1"]
    assert "ignored summary.json" in caplog.text


@pytest.mark.parametrize(
    "own_files",
    [
        {"metadata.json": {"experiment_name": "sweep"}},
        {"metadata.json": {"task_ids": ["aaa0001_1", "ccc0003_1"]}},
        {"results.json": {}},
        {"appworld_sdk_20260101_000000.json": {"note": "sweep"}},
        {"metadata.json": {"experiment_name": "sweep"}, "summary.json": {"total": 3}},
    ],
    ids=["metadata", "metadata-manifest", "results", "appworld_sdk", "metadata-and-summary"],
)
def test_a_parent_with_run_level_files_of_its_own_is_still_a_parent(tmp_path: Path, caplog: pytest.LogCaptureFixture, own_files: dict):
    # A sweep can keep its own config or summary at the top; with no task of its own it is not a run.
    runs = Path(shutil.copytree(FIXTURES, tmp_path / "runs"))
    for name, payload in own_files.items():
        (runs / name).write_text(json.dumps(payload))

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        assert list(by_id(CugaAdapter(), runs)) == ["aaa0001_1", "aaa0001_2", "ccc0003_1"]
    assert f"ignored {', '.join(sorted(own_files))}" in caplog.text


@pytest.mark.parametrize("metadata", ['{"task_ids": 3}', "{ truncated", "[]"], ids=["task_ids-not-a-list", "malformed", "not-an-object"])
def test_a_parent_with_a_bad_metadata_json_still_yields_its_child_runs(tmp_path: Path, caplog: pytest.LogCaptureFixture, metadata: str):
    runs = Path(shutil.copytree(FIXTURES, tmp_path / "runs"))
    (runs / "metadata.json").write_text(metadata)

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        assert list(by_id(CugaAdapter(), runs)) == ["aaa0001_1", "aaa0001_2", "ccc0003_1"]
    assert "ignored metadata.json" in caplog.text


@pytest.mark.parametrize(
    ("metadata", "error"),
    [('{"task_ids": 3}', "task_ids is not a list"), ("{ truncated", "Cannot read"), ("[]", "is not a JSON object")],
    ids=["task_ids-not-a-list", "malformed", "not-an-object"],
)
def test_reading_a_run_with_a_bad_metadata_json_fails_instead_of_guessing_its_tasks(run_copy: Path, metadata: str, error: str):
    # The manifest is authoritative: the glob would read zzz9999_1, which it leaves out.
    (run_copy / "metadata.json").write_text(metadata)

    with pytest.raises(ValueError, match=f"metadata.json.*{error}|{error}.*metadata.json"):
        list(CugaAdapter().records(run_copy))


def test_child_runs_beneath_a_run_are_named_in_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    # The path decides first: a task at the top makes it the run, but its child runs are not dropped silently.
    runs = Path(shutil.copytree(FIXTURES, tmp_path / "runs"))
    (runs / "example_task.json").write_text(json.dumps({"intent": "a copied example", "steps": []}))

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        assert list(by_id(CugaAdapter(), runs)) == ["example_task"]
    assert "not read: test_normal_dev, test_normal_train, which also hold tasks" in caplog.text


def _no_intents(run: Path) -> None:
    for name in ("aaa0001_1", "aaa0001_2"):
        task = json.loads((run / f"{name}.json").read_text())
        (run / f"{name}.json").write_text(json.dumps({**task, "intent": None}))


@pytest.mark.parametrize(
    "break_run",
    [
        _no_intents,  # zzz9999_1 still has one, but the manifest leaves it out, so detection must honor the manifest
        lambda run: (run / "metadata.json").write_text(json.dumps({"task_ids": ["gone_1", "gone_2"]})),
        lambda run: [(run / f"{name}.json").write_text("{ truncated") for name in ("aaa0001_1", "aaa0001_2")],
        lambda run: [(run / f"{name}.json").write_text("[1]") for name in ("aaa0001_1", "aaa0001_2")],
    ],
    ids=["no-intents-in-the-manifest", "manifest-names-missing-files", "malformed-tasks", "non-object-tasks"],
)
def test_a_child_with_json_but_no_readable_task_is_skipped_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture, break_run):
    runs = Path(shutil.copytree(FIXTURES, tmp_path / "runs"))
    break_run(runs / "test_normal_dev")

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        assert list(by_id(CugaAdapter(), runs)) == ["ccc0003_1"]
    assert "skipped test_normal_dev in" in caplog.text


@pytest.mark.parametrize("run", ["test_normal_dev", "test_normal_train", "bare"])
def test_a_json_subdirectory_inside_a_run_does_not_make_it_a_parent(tmp_path: Path, caplog: pytest.LogCaptureFixture, run: str):
    # dev has results.json and metadata.json, train only an appworld_sdk file, bare only its task files.
    single = Path(shutil.copytree(FIXTURES / ("test_normal_train" if run == "bare" else run), tmp_path / run))
    if run == "bare":
        (single / "appworld_sdk_20260101_000000.json").unlink()
    (single / "logs").mkdir()
    (single / "logs" / "trace.json").write_text("{}")

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        assert list(by_id(CugaAdapter(), single)) == (["ccc0003_1"] if run == "bare" else list(by_id(CugaAdapter(), FIXTURES / run)))
    assert "ignored" not in caplog.text


def test_a_directory_without_a_task_is_not_a_run(tmp_path: Path):
    # Only trace files, no task with an intent: an error, not a silent mine of 0 records.
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "trace.json").write_text("{}")
    (tmp_path / "summary.json").write_text(json.dumps({"total": 0}))

    with pytest.raises(ValueError, match="No CUGA run directory"):
        next(CugaAdapter().records(tmp_path))


def test_a_selection_that_reads_no_task_warns(run_copy: Path, caplog: pytest.LogCaptureFixture):
    task = json.loads((run_copy / "aaa0001_2.json").read_text())
    task["intent"] = None
    (run_copy / "aaa0001_2.json").write_text(json.dumps(task))

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        assert list(by_id(CugaAdapter(task_ids=frozenset({"aaa0001_2"})), run_copy)) == []
    assert "read no CUGA task" in caplog.text


@pytest.mark.parametrize("intent", [None, "", "  ", 5, ["a"], {"k": 1}])
def test_a_task_without_an_intent_is_skipped_with_a_warning(run_copy: Path, caplog: pytest.LogCaptureFixture, intent):
    task = json.loads((run_copy / "aaa0001_2.json").read_text())
    task["intent"] = intent
    (run_copy / "aaa0001_2.json").write_text(json.dumps(task))

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        assert list(by_id(CugaAdapter(), run_copy)) == ["aaa0001_1"]
    assert "skipped test_normal_dev/aaa0001_2: no intent" in caplog.text


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


def test_string_verdicts_are_parsed_not_coerced():
    adapter = CugaAdapter()
    failures = {"failures": [{"requirement": "r"}]}
    for verdict in ("False", "false", " FALSE "):
        for eval_ in ({"success": verdict, "evaluation": failures}, json.dumps({"success": verdict, "evaluation": failures})):
            outcome = adapter.record("run", "t_1", {"intent": "x", "steps": []}, {"eval": eval_}).outcome
            assert outcome is not None and (outcome.success, outcome.failed_checks) == (False, ("r",))
    assert adapter.record("run", "t_1", {"intent": "x", "steps": []}, {"eval": {"success": "True"}}).outcome == TrajectoryOutcome(
        success=True
    )

    # An unreadable verdict is unknown, so the next source in the documented order decides.
    step = {"name": "EvaluationResult", "data": json.dumps({"success": "false"})}
    task = {"intent": "x", "score": 1.0, "steps": [step]}
    assert adapter.record("run", "t_1", task, {"eval": {"success": "maybe"}}).outcome == TrajectoryOutcome(success=False)
    task = {"intent": "x", "score": 1.0, "steps": [{**step, "data": json.dumps({"success": 2})}]}
    assert adapter.record("run", "t_1", task, {"eval": {"success": "maybe"}}).outcome == TrajectoryOutcome(success=True)
    # The integers 0 and 1 are verdicts, not just numbers to fall past.
    for number, verdict in ((0, False), (1, True)):
        task = {"intent": "x", "score": float(not verdict), "steps": [step]}
        assert adapter.record("run", "t_1", task, {"eval": {"success": number}}).outcome == TrajectoryOutcome(success=verdict)
    assert adapter.record("run", "t_1", {"intent": "x", "steps": []}, {"eval": {"success": "maybe"}}).outcome is None


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


def test_long_trajectories_pass_through_whole_and_the_profile_keeps_their_ending():
    task = {"intent": "long task", "steps": [{"name": "Raw_Assistant_Response", "data": f"print({i})"} for i in range(70)]}
    record = CugaAdapter().record("run", "long_1", task, None)

    # The adapter no longer elides: the extractor's window decides what is rendered.
    assert record.messages[0] == {"role": "user", "content": "long task"}
    assert len(record.messages) == 1 + 70
    parsed = parse_openai_agents_trajectory(record.messages, options=profile_window(PROFILE))
    # The sample profile's tail keeps the last action past the 50-step budget.
    assert parsed["num_steps"] == 50  # 7 head + 43 tail; the marker is not a step
    assert parsed["steps_list"][parsed["marker_index"]].startswith("**[... 20 intermediate step(s) omitted; 70 steps")
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
    for name, text in [
        ("list.json", "[]"),
        ("ids_string.json", json.dumps({"task_ids": "aaa0001_1"})),
        ("tasks_not_objects.json", json.dumps({"tasks": ["aaa0001_1"]})),
        ("tasks_not_a_list.json", json.dumps({"tasks": "aaa0001_1"})),
        ("malformed.json", "{ truncated"),
    ]:
        (tmp_path / name).write_text(text)
        with pytest.raises(ValueError, match=re.escape(f"task manifest {tmp_path / name}")):
            CugaAdapter().configure({"task_manifest": str(tmp_path / name)})
    with pytest.raises(ValueError, match="Cannot read task manifest"):
        CugaAdapter().configure({"task_manifest": str(tmp_path / "missing.txt")})
    with pytest.raises(ValueError, match="Unknown cuga option 'rubric_mode'"):
        CugaAdapter().configure({"rubric_mode": "review"})
    with pytest.raises(ValueError, match="must be true or false"):
        CugaAdapter().configure({"include_summaries": "maybe"})


def test_partitions_keep_other_splits_out():
    # The fixture parent holds a dev and a train run; without a filter both are mined.
    train = CugaAdapter().configure({"partitions": "test_normal_train"})
    assert list(by_id(train)) == ["ccc0003_1"]
    both = CugaAdapter().configure({"partitions": "test_normal_dev, test_normal_train"})
    assert list(by_id(both)) == ["aaa0001_1", "aaa0001_2", "ccc0003_1"]

    with pytest.raises(ValueError, match=r"Partitions not found .*\['dev'\] \(found \['test_normal_dev', 'test_normal_train'\]\)"):
        next(CugaAdapter(partitions=frozenset({"dev"})).records(FIXTURES))
    # A selected task outside the selected partitions is not found, not silently dropped.
    with pytest.raises(ValueError, match=r"Task IDs not found .*\['aaa0001_1'\]"):
        next(train.configure({"task_ids": "aaa0001_1"}).records(FIXTURES))
    with pytest.raises(ValueError, match="cuga option partitions names nothing"):
        CugaAdapter().configure({"partitions": ""})


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
    assert (processor["config"]["trajectory_max_steps"], processor["config"]["trajectory_tail_steps"]) == (50, 43)


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
    # An empty filter (say, an unset shell variable) would mean "no filter", so it is refused.
    for empty in ("task_ids=", "task_ids= , "):
        assert cli.main([*argv, "--adapter-option", empty]) == 2
        assert "cuga option task_ids names nothing" in capsys.readouterr().err
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


# ---------------------------------------------------------------------------
# agent_view conversion mode and the evaluation-bundle layout.
# These share a synthetic bundle fixture that mimics CUGA's rendered-string
# prompt shape so the vendored agent_view helpers can split it back out.

BUNDLE = FIXTURES.parent / "cuga_bundle_demo"


def test_agent_view_shape_and_identity():
    recs = by_id(CugaAdapter().configure({"conversion": "agent_view"}), BUNDLE)

    r1 = recs["demo_01_1"]
    assert [m["role"] for m in r1.messages] == ["system", "user", "assistant", "user", "assistant"]
    # Bundle layout: run name is the bundle, partition is run.eval_key, model from env_vars.
    assert r1.batch.conversation_id == "cuga_bundle_demo/demo_01_1"
    assert r1.metadata["run"] == "cuga_bundle_demo"
    assert r1.metadata["partition"] == "test_normal_all_demo"
    assert r1.metadata["model"] == "Azure/gpt-4.1"
    # context_messages is always empty in agent_view regardless of other defaults.
    assert r1.context_messages == []


def test_agent_view_strips_user_context_from_the_task_message():
    r1 = by_id(CugaAdapter().configure({"conversion": "agent_view"}), BUNDLE)["demo_01_1"]

    assert r1.messages[1] == {"role": "user", "content": "List my open todos."}
    assert "## User Context" not in "".join(m.get("content", "") for m in r1.messages if isinstance(m.get("content"), str))


def test_agent_view_strips_available_variables_from_the_newest_user_message():
    r2 = by_id(CugaAdapter().configure({"conversion": "agent_view"}), BUNDLE)["demo_02_1"]

    all_content = "".join(m["content"] for m in r2.messages if m["role"] == "user")
    assert "## Available Variables" not in all_content


def test_agent_view_drops_the_fewshot_example_dialogue():
    r2 = by_id(CugaAdapter().configure({"conversion": "agent_view"}), BUNDLE)["demo_02_1"]

    # The fixture's example dialogue mentions candidates.txt; the agent-view messages must not.
    all_content = "".join(m.get("content") or "" for m in r2.messages if isinstance(m.get("content"), str))
    assert "candidates.txt" not in all_content


def test_agent_view_last_turn_output_from_step_data_not_classifier_prompt(tmp_path: Path):
    # agent_messages reads only the last call's input (which already contains the
    # whole history, so earlier turns' outputs come from the split rendered prompt)
    # plus the last call's own output as the final assistant message. The classifier
    # merge happens on the step that FOLLOWS a plain-text answer with auto_continue=true,
    # so the test contaminates the LAST Raw_Assistant_Response and verifies the fix
    # takes the output from `data`, not from `prompts[-1]`.
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))
    task_file = bundle / "trajectories" / "demo_02_1.json"
    task = json.loads(task_file.read_text())
    classifier_output = '{"auto_continue": true}'
    raws = [s for s in task["steps"] if s["name"] == "Raw_Assistant_Response" and s.get("prompts")]
    # Insert the classifier output as prompts[0] on the LAST agent call so prompts[-1]
    # still carries the agent's own answer, but prompts[0] is the merged classifier output.
    # A fallback through call_input_output would pick outputs[0] (the first assistant prompt),
    # which is now the classifier's — the fix must prefer step.data.
    raws[-1]["prompts"].insert(0, {"role": "assistant", "value": classifier_output})
    task_file.write_text(json.dumps(task))

    rec = by_id(CugaAdapter().configure({"conversion": "agent_view"}), bundle)["demo_02_1"]
    final_assistant = next(m for m in reversed(rec.messages) if m["role"] == "assistant")
    assert "auto_continue" not in final_assistant["content"]
    assert final_assistant["content"].startswith("I marked 'buy milk' as done")


def test_agent_view_outcome_is_inherited_from_the_steps_path():
    steps = by_id(CugaAdapter().configure({"conversion": "steps"}), BUNDLE)
    av = by_id(CugaAdapter().configure({"conversion": "agent_view"}), BUNDLE)

    for task_id in ("demo_01_1", "demo_02_1"):
        assert steps[task_id].outcome == av[task_id].outcome


def test_agent_view_revision_differs_from_steps_mode():
    steps = by_id(CugaAdapter().configure({"conversion": "steps"}), BUNDLE)
    av = by_id(CugaAdapter().configure({"conversion": "agent_view"}), BUNDLE)

    for task_id in ("demo_01_1", "demo_02_1"):
        assert steps[task_id].batch.revision != av[task_id].batch.revision


def test_agent_view_rejects_context_options():
    with pytest.raises(ValueError, match="cannot be combined with include_system_prompt"):
        CugaAdapter().configure({"conversion": "agent_view", "include_system_prompt": "true"})
    with pytest.raises(ValueError, match="cannot be combined with include_system_prompt"):
        CugaAdapter().configure({"conversion": "agent_view", "include_summaries": "true"})
    with pytest.raises(ValueError, match="conversion must be one of"):
        CugaAdapter().configure({"conversion": "whatever"})


def test_agent_view_skips_long_runs_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))
    # Inflate demo_01_1 by stuffing the last agent call's recovered input with
    # _AGENT_VIEW_STEP_LIMIT+1 assistant turns. Combined with the final appended
    # assistant output, that is _AGENT_VIEW_STEP_LIMIT+2 assistant messages, past the cap.
    task_file = bundle / "trajectories" / "demo_01_1.json"
    task = json.loads(task_file.read_text())
    last_call = [s for s in task["steps"] if s["name"] == "Raw_Assistant_Response" and s.get("prompts")][-1]
    rendered_prompt = next(p for p in last_call["prompts"] if p["role"] == "system" and p["value"].startswith("System:"))
    extra_turns = "".join(f"\n\nAI: filler reply {i}\n\nHuman: filler query {i}" for i in range(_AGENT_VIEW_STEP_LIMIT + 1))
    rendered_prompt["value"] = rendered_prompt["value"] + extra_turns
    task_file.write_text(json.dumps(task))

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        recs = by_id(CugaAdapter().configure({"conversion": "agent_view"}), bundle)
    assert list(recs) == ["demo_02_1"]
    assert "exceed the parser cap" in caplog.text


def test_agent_view_skips_tasks_with_no_main_agent_call(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))
    task_file = bundle / "trajectories" / "demo_02_1.json"
    task = json.loads(task_file.read_text())
    for step in task["steps"]:
        if step["name"] == "Raw_Assistant_Response":
            step["prompts"] = []
    task_file.write_text(json.dumps(task))

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        recs = by_id(CugaAdapter().configure({"conversion": "agent_view"}), bundle)
    assert list(recs) == ["demo_01_1"]
    assert "skipped evaluation_bundle/demo_02_1" in caplog.text


def test_bundle_uses_sdk_report_when_task_eval_is_missing(tmp_path: Path):
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))
    # Remove the EvaluationResult step and the score so only the SDK report carries a verdict.
    task_file = bundle / "trajectories" / "demo_01_1.json"
    task = json.loads(task_file.read_text())
    task["steps"] = [s for s in task["steps"] if s["name"] != "EvaluationResult"]
    task["score"] = None
    task_file.write_text(json.dumps(task))

    rec = by_id(CugaAdapter().configure({"conversion": "agent_view"}), bundle)["demo_01_1"]
    assert rec.outcome is not None and rec.outcome.success is True


def test_bundle_model_default_from_env_vars(tmp_path: Path):
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))

    without_option = by_id(CugaAdapter(), bundle)["demo_01_1"]
    with_option = by_id(CugaAdapter(model="override").configure({}), bundle)["demo_01_1"]

    assert without_option.metadata["model"] == "Azure/gpt-4.1"
    assert with_option.metadata["model"] == "override"


def test_bundle_warns_when_context_summarization_enabled_only_in_agent_view(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    # The warning is about agent_view resampling prefixes, so steps mode does not
    # need to see it. Fires only when conversion=agent_view is requested.
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))
    metadata = json.loads((bundle / "metadata.json").read_text())
    metadata["runtime_config"]["env_vars"]["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = "true"
    (bundle / "metadata.json").write_text(json.dumps(metadata))

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        list(CugaAdapter().records(bundle))
    assert "context summarization" not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        list(CugaAdapter().configure({"conversion": "agent_view"}).records(bundle))
    assert "context summarization" in caplog.text


def test_bundle_companion_files_in_trajectories_are_ignored(tmp_path: Path):
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))
    (bundle / "trajectories" / "demo_01_1.calls.json").write_text("[]")
    (bundle / "trajectories" / "demo_01_1.agent_messages.json").write_text("[]")

    assert list(by_id(CugaAdapter(), bundle)) == ["demo_01_1", "demo_02_1"]


def test_legacy_run_layout_still_works():
    # Regression guard: the existing fixtures (which pre-date the bundle layout) must
    # keep producing records with partition parsed from the directory name.
    recs = by_id(CugaAdapter(), DEV)

    assert list(recs) == ["aaa0001_1", "aaa0001_2"]
    assert recs["aaa0001_1"].metadata["partition"] == "test_normal_dev"
    assert recs["aaa0001_1"].batch.conversation_id == "test_normal_dev/aaa0001_1"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda t: {**t, "steps": None}, id="steps-null"),
        pytest.param(lambda t: {**t, "steps": [{**t["steps"][0], "prompts": "not-a-list"}, *t["steps"][1:]]}, id="prompts-not-list"),
        pytest.param(
            lambda t: {**t, "steps": [{**t["steps"][0], "prompts": [{"role": "system"}]}, *t["steps"][1:]]},
            id="prompt-entry-missing-value",
        ),
        pytest.param(
            lambda t: {**t, "steps": [{**t["steps"][0], "prompts": [{"role": "system", "value": None}]}, *t["steps"][1:]]},
            id="prompt-value-null",
        ),
        pytest.param(
            lambda t: {**t, "steps": [{**t["steps"][0], "prompts": ["not-a-dict"]}, *t["steps"][1:]]},
            id="prompt-entry-non-dict",
        ),
    ],
)
def test_agent_view_bad_task_skipped_without_stopping_the_generator(tmp_path: Path, caplog: pytest.LogCaptureFixture, mutate):
    # One malformed task in a bundle must not stop records() from yielding the next.
    # Covers the shape errors (ValueError/KeyError/TypeError/AttributeError) raised
    # by the vendored helpers when prompts are not what they expect.
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))
    task_file = bundle / "trajectories" / "demo_01_1.json"
    task = json.loads(task_file.read_text())
    task_file.write_text(json.dumps(mutate(task)))

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        recs = list(CugaAdapter().configure({"conversion": "agent_view"}).records(bundle))
    assert [r.trace_id for r in recs] == ["demo_02_1"]
    assert any("skipped" in record.message and "demo_01_1" in record.message for record in caplog.records)


def test_agent_view_raises_when_last_agent_call_has_no_prompts(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    # Silent truncation hides agent work from the generator while the outcome still
    # describes the full run — the helper must raise, and the adapter must skip the task.
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))
    task_file = bundle / "trajectories" / "demo_02_1.json"
    task = json.loads(task_file.read_text())
    # Strip prompts from the last Raw_Assistant_Response step while keeping the first
    # with prompts intact; the final one has `data` so split_messages would otherwise
    # still produce a (shorter) record. The helper must detect the missing prompts.
    last_raw_idx = max(i for i, s in enumerate(task["steps"]) if s["name"] == "Raw_Assistant_Response")
    task["steps"][last_raw_idx]["prompts"] = []
    task_file.write_text(json.dumps(task))

    with caplog.at_level(logging.WARNING, logger="experiments.guideline_pipeline.adapters.cuga"):
        recs = list(CugaAdapter().configure({"conversion": "agent_view"}).records(bundle))
    assert [r.trace_id for r in recs] == ["demo_01_1"]
    assert any("demo_02_1" in record.message and "last" in record.message.lower() for record in caplog.records)


def test_agent_view_strip_user_context_keeps_later_sections():
    from experiments.guideline_pipeline.adapters.cuga_agent_view import strip_user_context

    content = (
        "Buy milk\n\n## User Context\nSupervisor: Alice\nCurrent datetime: 2026-10-20\n\n## Additional instructions\nUse the shortest path"
    )
    stripped = strip_user_context(content)
    assert "## User Context" not in stripped
    assert "## Additional instructions" in stripped
    assert "shortest path" in stripped
    # Block at the end (no later section) still works.
    assert strip_user_context("Buy milk\n\n## User Context\nSupervisor: Alice") == "Buy milk"
    # No block at all leaves the content unchanged.
    assert strip_user_context("Buy milk") == "Buy milk"


def test_agent_view_task_message_index_picks_the_real_task_not_a_fewshot_prefix():
    # Few-shot example dialogue can contain a user message whose first words match the
    # intent. task_message_index must match the exact intent line to find the real task.
    from experiments.guideline_pipeline.adapters.cuga_agent_view import task_message_index

    messages = [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "Book a flight for the example user."},  # a few-shot
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "Book a flight"},  # the real task
    ]
    assert task_message_index(messages, "Book a flight") == 3
    # When the intent is longer than any user message, no match is found.
    with pytest.raises(ValueError, match="task message not found"):
        task_message_index(messages, "Book a flight to Paris")


def test_agent_view_task_message_index_handles_multi_line_intent():
    # A CUGA task formatted as "<intent>\n\n## User Context..." may carry a multi-line
    # intent. task_message_index must match the first line of the intent against the
    # first line of the user message, so the task is still found. A full-string compare
    # would silently drop every such task to a "task message not found" skip.
    from experiments.guideline_pipeline.adapters.cuga_agent_view import task_message_index

    multi_line_intent = "Line 1 of the task\nLine 2 with more detail"
    messages = [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "Book a flight for the example user."},  # a few-shot
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": multi_line_intent + "\n\n## User Context\n..."},
    ]
    assert task_message_index(messages, multi_line_intent) == 3


def test_bundle_task_id_with_dot_still_read():
    # Companion files use known suffixes (.calls.json, .agent_messages.json). A legacy
    # task id that itself contains a dot ("appworld.v2_01.json") must remain a task.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(shutil.copytree(BUNDLE, Path(tmp) / "evaluation_bundle"))
        src = bundle / "trajectories" / "demo_01_1.json"
        src.rename(bundle / "trajectories" / "appworld.v2_01.json")
        # Rename only the file; the intent inside stays, so records() finds it.
        assert "appworld.v2_01" in {t.stem for t in (bundle / "trajectories").glob("*.json")}
        recs = by_id(CugaAdapter(), bundle)
        assert "appworld.v2_01" in recs


def test_bundle_with_unreadable_metadata_fails_loudly(tmp_path: Path):
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))
    (bundle / "metadata.json").write_text("{ not json ")
    # Reading a single corrupted bundle raises.
    with pytest.raises(ValueError, match=r"metadata.json"):
        list(CugaAdapter().records(bundle))


def test_bundle_verdict_precedence_sdk_over_legacy(tmp_path: Path):
    # README: SDK → legacy → EvaluationResult step → score. If a legacy results.json entry
    # exists but has no readable `success`, the SDK verdict wins (not None).
    bundle = Path(shutil.copytree(BUNDLE, tmp_path / "evaluation_bundle"))
    # Write a legacy results.json with an unreadable success for demo_02_1; the SDK
    # report in the bundle says success=False.
    (bundle / "results.json").write_text(json.dumps({"demo_02_1": {"eval": json.dumps({"success": "unknown"})}}))
    # Remove the EvaluationResult step so it can't take precedence.
    task_file = bundle / "trajectories" / "demo_02_1.json"
    task = json.loads(task_file.read_text())
    task["steps"] = [s for s in task["steps"] if s["name"] != "EvaluationResult"]
    task["score"] = None
    task_file.write_text(json.dumps(task))

    rec = by_id(CugaAdapter(), bundle)["demo_02_1"]
    assert rec.outcome is not None and rec.outcome.success is False
