"""The AppWorld adapter, on hand-written synthetic run directories under fixtures/appworld."""

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
from experiments.guideline_pipeline.adapters.appworld import AppWorldAdapter, parse_environment_io, task_from_prompt
from experiments.guideline_pipeline.adapters.cuga import PARSER_STEP_LIMIT
from experiments.guideline_pipeline.stages.mine import mine

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "appworld"
TRAIN = FIXTURES / "react_train"
PLAYBOOK = FIXTURES / "playbook_test_normal"
PROFILE = Path(__file__).parents[1] / "profiles" / "appworld.json"
IDENTITY = "My name is: Test User. My personal email is test.user@example.com and phone number is 0000000000."


def by_id(adapter: AppWorldAdapter, path: Path = FIXTURES) -> dict[str, AdapterRecord]:
    return {record.trace_id: record for record in adapter.records(path)}


@pytest.fixture
def run_copy(tmp_path: Path) -> Path:
    return Path(shutil.copytree(TRAIN, tmp_path / "react_train"))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An AppWorld root holding the one specs.json the fixtures need (built here: experiments/ ignores data/)."""
    specs = tmp_path / "appworld_root" / "data" / "tasks" / "bbb0002_3" / "specs.json"
    specs.parent.mkdir(parents=True)
    specs.write_text(json.dumps({"instruction": "List the titles of my playlists.", "supervisor": {"first_name": "Test"}}))
    return tmp_path / "appworld_root"


def test_registered_and_discovers_one_or_many_runs(tmp_path: Path):
    assert isinstance(ADAPTERS["appworld"], AppWorldAdapter)
    assert list(by_id(AppWorldAdapter())) == ["bbb0002_1", "bbb0002_2", "aaa0001_1", "aaa0001_2"]
    assert list(by_id(AppWorldAdapter(), TRAIN)) == ["aaa0001_1", "aaa0001_2"]
    with pytest.raises(ValueError, match="No AppWorld run directory"):
        next(AppWorldAdapter().records(tmp_path))
    with pytest.raises(ValueError, match="No AppWorld run directory"):
        next(AppWorldAdapter().records(tmp_path / "missing"))


def test_messages_are_rebuilt_in_the_extractor_shape():
    record = by_id(AppWorldAdapter(), TRAIN)["aaa0001_1"]

    assert record.messages[0] == {"role": "user", "content": "List my open todos."}
    call = record.messages[1]["content"][0]
    assert call["type"] == "function_call" and call["id"] == "call_0"
    assert call["function"] == {
        "name": "execute_ipython",
        "arguments": json.dumps({"code": 'todos = apis.todo.show_todos(status="open")\nprint(todos)'}),
    }
    assert record.messages[2] == {"role": "assistant", "content": 'OBSERVATION:\n[{"id": 1, "text": "buy milk"}]'}
    assert record.messages[3]["content"][0]["id"] == "call_1"
    assert record.messages[4] == {"role": "assistant", "content": "OBSERVATION:\nExecution successful."}
    assert len(record.messages) == 5 and record.context_messages == []

    parsed = parse_openai_agents_trajectory(record.messages)
    assert parsed["task_instruction"] == "List my open todos."
    assert parsed["num_steps"] == 4
    assert parsed["steps_list"][0].startswith("**Step 1 - Action:**\nexecute_ipython(code=")


def test_environment_io_parsing_and_step_cut():
    text = "### Environment Interaction 1\n```python\nprint(1)\n```\n\n```\n\n```\n### Environment Interaction 2\n-----\n```python\nx\n```\n```\ny\n```"
    # The dashes line is optional and an empty observation is kept here, dropped as a step later.
    assert parse_environment_io(text) == [
        {"name": "Raw_Assistant_Response", "data": "print(1)"},
        {"name": "User_output", "data": ""},
        {"name": "Raw_Assistant_Response", "data": "x"},
        {"name": "User_output", "data": "y"},
    ]
    long = f"### Environment Interaction 1\n```python\n{'a' * 5000}\n```\n```\nok\n```"
    record = AppWorldAdapter().record("run", "t_1", instruction="x", instruction_source="tips_subtask", environment_io=long)
    assert len(json.loads(record.messages[1]["content"][0]["function"]["arguments"])["code"]) == 2000


def test_instruction_sources_in_order(root: Path):
    records = by_id(AppWorldAdapter(appworld_root=root))
    sources = {task_id: (record.messages[0]["content"], record.metadata["instruction_source"]) for task_id, record in records.items()}

    assert sources == {
        # tips_subtask.json wins, though its instruction lacks the identity line the prompt has.
        "aaa0001_1": ("List my open todos.", "tips_subtask"),
        # The last user message of the first LM call, not a few-shot example or a later call.
        "aaa0001_2": (f"{IDENTITY}\nTask: Mark the todo 'buy milk' as done.", "lm_calls"),
        # A playbook before the task (with its own "Task:" mention) is left out.
        "bbb0002_1": (f"{IDENTITY}\nTask: Share my note 'groceries' with my roommate.", "lm_calls"),
        # The first prompt has no "Task:", so logger.jsonl's task entry is used.
        "bbb0002_2": ("Say hello.", "logger"),
        # Nothing in the run: specs.json under appworld_root.
        "bbb0002_3": ("List the titles of my playlists.", "specs"),
    }


def test_task_slicing():
    padding = "x" * 700
    assert task_from_prompt(f"preamble\n\n{IDENTITY}\nTask: Do it.\n\n") == f"{IDENTITY}\nTask: Do it."
    # The identity line is too far back, so 200 characters before "Task:" are kept instead.
    assert task_from_prompt(f"My name is: Someone.\n{padding}\nTask: Do it.") == "x" * 199 + "\nTask: Do it."
    assert task_from_prompt("Task: Do it.") == "Task: Do it."
    # The last "Task:" wins over example tasks and over a "My name is:" after it.
    assert task_from_prompt(f"{IDENTITY}\nTask: example.\n\nMy name is: B.\nTask: real.") == "My name is: B.\nTask: real."
    # No "Task:" at all is no task, not an arbitrary tail of the prompt.
    assert task_from_prompt("a long playbook with no task marker") is None
    assert task_from_prompt("Task:   ") == "Task:"


def test_tasks_that_cannot_be_mined_are_skipped_and_reported(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING):
        records = by_id(AppWorldAdapter(), PLAYBOOK)

    assert list(records) == ["bbb0002_1", "bbb0002_2"]
    assert "skipped playbook_test_normal/bbb0002_3: no task instruction found" in caplog.messages
    assert "skipped playbook_test_normal/bbb0002_4: no logs/environment_io.md" in caplog.messages


def test_environment_io_without_interactions_is_skipped(run_copy: Path, caplog: pytest.LogCaptureFixture):
    (run_copy / "tasks" / "aaa0001_2" / "logs" / "environment_io.md").write_text("# nothing ran\n")
    with caplog.at_level(logging.WARNING):
        assert list(by_id(AppWorldAdapter(), run_copy)) == ["aaa0001_1"]
    assert "skipped react_train/aaa0001_2: no environment interactions in logs/environment_io.md" in caplog.messages


def test_outcome_from_typed_or_string_evaluations(root: Path):
    records = by_id(AppWorldAdapter(appworld_root=root))

    assert records["aaa0001_1"].outcome == TrajectoryOutcome(success=True)
    assert records["aaa0001_2"].outcome == TrajectoryOutcome(success=False)
    # "False" / "True" strings, with string num_tests and Python-repr passes/failures.
    assert records["bbb0002_1"].outcome == TrajectoryOutcome(success=False)
    assert records["bbb0002_3"].outcome == TrajectoryOutcome(success=True)
    # Not in the evaluation: no outcome, not a guessed failure.
    assert records["bbb0002_2"].outcome is None

    adapter = AppWorldAdapter()
    # The CUGA adapter's verdict rule: a bool, 0 or 1, or "true"/"false" in any case; bool("False") is True.
    for verdict in ("maybe", None, 2, "", ["True"], "yes"):
        evaluation = ("train", {"success": verdict})
        record = adapter.record("run", "t_1", instruction="x", instruction_source="tips_subtask", environment_io="", evaluation=evaluation)
        assert record.outcome is None
    lowered = adapter.record(
        "run", "t_1", instruction="x", instruction_source="s", environment_io="", evaluation=("train", {"success": " true "})
    )
    assert lowered.outcome == TrajectoryOutcome(success=True)
    for value, success in ((1, True), (0, False)):
        evaluation = ("train", {"success": value})
        numeric = adapter.record("run", "t_1", instruction="x", instruction_source="s", environment_io="", evaluation=evaluation)
        assert numeric.outcome == TrajectoryOutcome(success=success)


def test_identity_metadata_and_model():
    records = by_id(AppWorldAdapter(model="gpt-4.1"))

    record = records["aaa0001_2"]
    assert (record.batch.source, record.batch.conversation_id, record.batch.batch_id) == ("appworld", "react_train/aaa0001_2", "aaa0001_2")
    assert record.model == "gpt-4.1"
    # The split is the evaluations file name, not the run directory name.
    assert record.metadata == {
        "run": "react_train",
        "split": "train",
        "difficulty": 2,
        "model": "gpt-4.1",
        "instruction_source": "lm_calls",
    }
    assert records["bbb0002_1"].metadata == {
        "run": "playbook_test_normal",
        "split": "test_normal",
        "difficulty": 2,
        "model": "gpt-4.1",
        "instruction_source": "lm_calls",
    }
    assert records["bbb0002_2"].metadata == {"run": "playbook_test_normal", "model": "gpt-4.1", "instruction_source": "logger"}


def test_a_task_evaluated_twice_is_an_error(run_copy: Path):
    shutil.copy(run_copy / "evaluations" / "train.json", run_copy / "evaluations" / "dev.json")
    with pytest.raises(ValueError, match="Task aaa0001_1 is evaluated in both dev.json and train.json"):
        next(AppWorldAdapter().records(run_copy))


def test_batch_is_stable_and_the_revision_follows_content(run_copy: Path):
    first = by_id(AppWorldAdapter(), run_copy)
    assert by_id(AppWorldAdapter(), run_copy) == first

    log = run_copy / "tasks" / "aaa0001_2" / "logs" / "environment_io.md"
    log.write_text(log.read_text().replace('status="fail"', 'status="success"'))
    edited = by_id(AppWorldAdapter(), run_copy)
    assert edited["aaa0001_1"].batch == first["aaa0001_1"].batch
    assert edited["aaa0001_2"].batch.batch_id == first["aaa0001_2"].batch.batch_id
    assert edited["aaa0001_2"].batch.revision != first["aaa0001_2"].batch.revision

    evaluation = run_copy / "evaluations" / "train.json"
    payload = json.loads(evaluation.read_text())
    payload["individual"]["aaa0001_1"]["success"] = "False"
    evaluation.write_text(json.dumps(payload))
    regraded = by_id(AppWorldAdapter(), run_copy)
    assert regraded["aaa0001_1"].batch.revision != first["aaa0001_1"].batch.revision

    # Recording the model changes what processors see, so it is a new revision too.
    assert by_id(AppWorldAdapter(model="m"), run_copy)["aaa0001_2"].batch.revision != edited["aaa0001_2"].batch.revision


def test_long_runs_keep_head_and_tail():
    environment_io = "\n".join(f"### Environment Interaction {i}\n```python\nprint({i})\n```\n\n```\n{i}\n```\n" for i in range(40))
    record = AppWorldAdapter().record("run", "long_1", instruction="long task", instruction_source="s", environment_io=environment_io)

    assert record.messages[0] == {"role": "user", "content": "long task"}
    parsed = parse_openai_agents_trajectory(record.messages)
    assert parsed["num_steps"] == PARSER_STEP_LIMIT
    assert parsed["steps_list"][6] == "**Step 7 - Reasoning:**\n[31 steps elided: the trajectory continues below]"
    assert parsed["steps_list"][-1] == "**Step 50 - Reasoning:**\nOBSERVATION:\n39"


def test_options_and_task_selection(tmp_path: Path, root: Path):
    manifest = tmp_path / "tasks.txt"
    manifest.write_text("# selected\naaa0001_2\n")
    adapter = get_adapter("appworld", {"task_manifest": str(manifest), "task_ids": "bbb0002_3", "model": "m", "appworld_root": str(root)})

    assert isinstance(adapter, AppWorldAdapter) and adapter is not ADAPTERS["appworld"]
    assert (adapter.model, adapter.appworld_root) == ("m", root)
    assert list(by_id(adapter)) == ["bbb0002_3", "aaa0001_2"]
    json_manifest = tmp_path / "tasks.json"
    json_manifest.write_text(json.dumps({"task_ids": ["aaa0001_1"]}))
    assert list(by_id(AppWorldAdapter().configure({"task_manifest": str(json_manifest)}))) == ["aaa0001_1"]

    with pytest.raises(ValueError, match=r"Task IDs not found beneath .*\['nope_1'\]"):
        next(AppWorldAdapter(task_ids=frozenset({"nope_1", "aaa0001_1"})).records(FIXTURES))
    with pytest.raises(ValueError, match="Unknown appworld option 'include_summaries'"):
        AppWorldAdapter().configure({"include_summaries": "true"})
    with pytest.raises(ValueError, match="appworld_root must be a directory"):
        AppWorldAdapter().configure({"appworld_root": str(tmp_path / "missing")})


def test_records_stream_one_task_at_a_time(run_copy: Path):
    records = AppWorldAdapter().records(run_copy)

    assert isinstance(records, Iterator)
    assert next(records).trace_id == "aaa0001_1"
    # The second task is not read until it is asked for, so this edit is what it sees.
    (run_copy / "tasks" / "aaa0001_2" / "logs" / "environment_io.md").write_text("# nothing ran\n")
    assert list(records) == []


def test_sample_profile_validates_with_the_builtin_processor():
    plan = ProcessingManager().validate(json.loads(PROFILE.read_text()))
    (processor,) = plan.manifest()["processors"]
    assert processor["plugin"] == "evolve.guidelines"
    assert (processor["config"]["guidelines_mode"], processor["config"]["segmentation_enabled"]) == ("standard", False)


def test_mine_dry_run_from_the_cli(root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def no_client() -> EvolveClient:
        raise AssertionError("dry run must not touch storage")

    monkeypatch.setattr(cli, "make_client", no_client)
    argv = ["mine", "--adapter", "appworld", "--input", str(FIXTURES), "--namespace", "x", "--processing-profile", "x", "--dry-run"]

    assert cli.main(argv) == 0
    assert capsys.readouterr().out.strip() == "dry run: 4 valid, 0 failed"
    assert cli.main([*argv, "--adapter-option", f"appworld_root={root}"]) == 0
    assert capsys.readouterr().out.strip() == "dry run: 5 valid, 0 failed"
    assert cli.main([*argv, "--adapter-option", "task_ids=aaa0001_1"]) == 0
    assert capsys.readouterr().out.strip() == "dry run: 1 valid, 0 failed"
    # Records are read lazily, so a missing task ends the run as an adapter failure.
    assert cli.main([*argv, "--adapter-option", "task_ids=nope_1"]) == 1
    assert "failed <adapter>: ValueError: Task IDs not found" in capsys.readouterr().err
    assert cli.main([*argv, "--adapter-option", "nope=1"]) == 2
    assert "Unknown appworld option 'nope'" in capsys.readouterr().err


def test_mine_checkpoints_each_task_and_stamps_evidence(client: EvolveClient, run_copy: Path):
    def run():
        return mine(AppWorldAdapter().records(run_copy), client, namespace_id="memories", processing_profile="echo")

    first, again = run(), run()
    assert (first.processed, first.skipped, again.processed, again.skipped) == (2, 0, 0, 2)
    stored = {e.metadata["processing"]["source_batch"]["batch_id"]: e for e in client.get_all_entities("memories")}
    assert {batch: e.metadata["success"] for batch, e in stored.items()} == {"aaa0001_1": True, "aaa0001_2": False}

    evaluation = run_copy / "evaluations" / "train.json"
    payload = json.loads(evaluation.read_text())
    payload["individual"]["aaa0001_2"]["success"] = "True"
    evaluation.write_text(json.dumps(payload))
    regraded = run()
    assert (regraded.processed, regraded.skipped) == (1, 1)


def blk(number: int, code: str, output: str) -> str:
    return f"### Environment Interaction {number}\n----------\n```python\n{code}\n```\n\n```\n{output}\n```\n\n\n"


def test_empty_option_values_are_errors_not_no_filter():
    for options in ({"task_ids": ""}, {"task_ids": " , "}, {"splits": ""}, {"splits": ","}):
        with pytest.raises(ValueError, match="names nothing"):
            AppWorldAdapter().configure(options)
    # Path("") is the working directory, so an empty root would silently search it.
    with pytest.raises(ValueError, match="appworld_root must be a directory"):
        AppWorldAdapter().configure({"appworld_root": " "})


def test_unreadable_instruction_sources_fall_through_with_a_warning(run_copy: Path, root: Path, caplog: pytest.LogCaptureFixture):
    logs = run_copy / "tasks" / "aaa0001_2" / "logs"
    (run_copy / "tasks" / "aaa0001_2" / "tips_subtask.json").write_text("{ truncated")
    with caplog.at_level(logging.WARNING):
        records = by_id(AppWorldAdapter(), run_copy)
    assert records["aaa0001_2"].metadata["instruction_source"] == "lm_calls"
    assert any(m.startswith("react_train/aaa0001_2: cannot read tips_subtask.json") for m in caplog.messages)

    # A truncated first LM call with nothing after it: the task is skipped, and the run goes on.
    (logs / "lm_calls.jsonl").write_text("{truncated\n")
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert list(by_id(AppWorldAdapter(), run_copy)) == ["aaa0001_1"]
    assert any(m.startswith("react_train/aaa0001_2: cannot read lm_calls.jsonl") for m in caplog.messages)
    assert "skipped react_train/aaa0001_2: no task instruction found" in caplog.messages

    # logger.jsonl and specs.json are read the same way.
    (logs / "logger.jsonl").write_text('{"role": "agent"\n')
    specs = root / "data" / "tasks" / "aaa0001_2" / "specs.json"
    specs.parent.mkdir(parents=True)
    specs.write_text("[not an object]")
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert list(by_id(AppWorldAdapter(appworld_root=root), run_copy)) == ["aaa0001_1"]
    assert any(m.startswith("react_train/aaa0001_2: cannot read logger.jsonl") for m in caplog.messages)
    assert any(m.startswith("react_train/aaa0001_2: cannot read specs.json") for m in caplog.messages)


def test_malformed_evaluations_name_the_file(run_copy: Path):
    evaluation = run_copy / "evaluations" / "train.json"
    for text, reason in (("{ truncated", "Expecting"), ("[]", "not a JSON object"), ('{"aggregate": {}}', "no 'individual' mapping")):
        evaluation.write_text(text)
        with pytest.raises(ValueError, match=rf"Cannot read {re.escape(str(evaluation))}: .*{reason}"):
            next(AppWorldAdapter().records(run_copy))


def test_unreadable_environment_io_is_skipped(run_copy: Path, caplog: pytest.LogCaptureFixture):
    (run_copy / "tasks" / "aaa0001_2" / "logs" / "environment_io.md").write_bytes(b"\xff\xfe not utf-8")
    with caplog.at_level(logging.WARNING):
        assert list(by_id(AppWorldAdapter(), run_copy)) == ["aaa0001_1"]
    assert any(m.startswith("skipped react_train/aaa0001_2: cannot read logs/environment_io.md") for m in caplog.messages)


def test_an_observation_holding_a_fenced_block_is_kept_whole():
    markdown = "Here:\n```\ninner\n```\nend of doc"
    steps = parse_environment_io(blk(1, "print(doc)", markdown) + blk(2, "apis.supervisor.complete_task()", "done"))
    assert [step["data"] for step in steps] == ["print(doc)", markdown, "apis.supervisor.complete_task()", "done"]
    # An interaction missing its output fence does not swallow the next one.
    broken = "### Environment Interaction 1\n```python\nx\n```\n\n```\nno closing fence\n\n"
    assert [step["data"] for step in parse_environment_io(broken + blk(2, "y", "z"))] == ["y", "z"]


def test_non_string_prompt_content_is_not_an_instruction(run_copy: Path):
    lm_calls = run_copy / "tasks" / "aaa0001_2" / "logs" / "lm_calls.jsonl"
    for content in ({"text": "Task: from a dict"}, [{"type": "text", "text": {"Task:": "x"}}], 7):
        lm_calls.write_text(json.dumps({"input": {"messages": [{"role": "user", "content": content}]}}) + "\n")
        assert "aaa0001_2" not in by_id(AppWorldAdapter(), run_copy)
    parts = [{"type": "text", "text": "My name is: A.\n"}, {"type": "image"}, {"type": "text", "text": "Task: from parts."}]
    lm_calls.write_text(json.dumps({"input": {"messages": [{"role": "user", "content": parts}]}}) + "\n")
    assert by_id(AppWorldAdapter(), run_copy)["aaa0001_2"].messages[0]["content"] == "My name is: A.\n\nTask: from parts."


def test_layouts_that_read_nothing_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    # AppWorld's legacy agents nest runs as <agent>/<provider>/<model>/<split>/tasks.
    shutil.copytree(TRAIN, tmp_path / "outputs" / "legacy_react_code_agent" / "openai" / "gpt-4o" / "test_normal")
    shutil.copytree(TRAIN, tmp_path / "outputs" / "react_train")
    with caplog.at_level(logging.WARNING):
        assert list(by_id(AppWorldAdapter(), tmp_path / "outputs")) == ["aaa0001_1", "aaa0001_2"]
    assert f"ignored legacy_react_code_agent in {tmp_path / 'outputs'}: no tasks/ directory" in caplog.messages

    empty = tmp_path / "empty_run"
    (empty / "tasks").mkdir(parents=True)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert list(by_id(AppWorldAdapter(), empty)) == []
    assert f"read no AppWorld task beneath {empty}" in caplog.messages


def test_splits_option_selects_evaluated_tasks_by_split(caplog: pytest.LogCaptureFixture):
    assert list(by_id(AppWorldAdapter().configure({"splits": "train"}))) == ["aaa0001_1", "aaa0001_2"]
    # bbb0002_2 has no evaluation, so its split is unknown and a split filter leaves it out.
    assert list(by_id(AppWorldAdapter().configure({"splits": "test_normal"}))) == ["bbb0002_1"]
    assert list(by_id(AppWorldAdapter().configure({"splits": "train, test_normal"}))) == ["bbb0002_1", "aaa0001_1", "aaa0001_2"]

    with pytest.raises(ValueError, match=r"Splits not found beneath .*: \['dev'\] \(found \['test_normal', 'train'\]\)"):
        next(AppWorldAdapter().configure({"splits": "train,dev"}).records(FIXTURES))
    # A selected task outside the selected splits is not found, rather than silently dropped.
    with pytest.raises(ValueError, match=r"Task IDs not found beneath .*\['bbb0002_1'\]"):
        next(AppWorldAdapter().configure({"splits": "train", "task_ids": "bbb0002_1"}).records(FIXTURES))


def test_mining_test_split_tasks_without_naming_them_warns(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING):
        by_id(AppWorldAdapter())
    assert "read test-split tasks from playbook_test_normal (test_normal); pass splits= to choose splits" in caplog.messages
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        by_id(AppWorldAdapter().configure({"splits": "test_normal"}))
        by_id(AppWorldAdapter(), TRAIN)
    assert not [message for message in caplog.messages if "test-split" in message]
