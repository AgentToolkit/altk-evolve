"""The tau-retail adapter, on hand-written synthetic task files: no LLM, no network."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from altk_evolve.config.guideline_runtime import GuidelineRuntime
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.llm.guidelines.guidelines import parse_openai_agents_trajectory

from experiments.guideline_pipeline import __main__ as cli
from experiments.guideline_pipeline.adapters import ADAPTERS, get_adapter
from experiments.guideline_pipeline.adapters.tau import (
    TauRetailAdapter,
    Turn,
    judge_outcome,
    parse_conversation,
    parse_header,
    section_body,
    split_sections,
)
from experiments.guideline_pipeline.stages.mine import mine

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "tau"


@pytest.fixture
def tau_dir(tmp_path: Path) -> Path:
    """A writable copy of the fixtures, for tests that edit or add files."""
    return Path(shutil.copytree(FIXTURES, tmp_path / "tau"))


def records(path: Path = FIXTURES, **options) -> dict:
    return {record.trace_id: record for record in TauRetailAdapter(**options).records(path)}


def conversation(*lines: str) -> str:
    return "CONVERSATION\n------------\n" + "\n".join(lines)


def task_file(directory: Path, name: str, turns: list[str]) -> Path:
    text = (FIXTURES / "task_1.txt").read_text()
    head, _, rest = text.partition("CONVERSATION\n------------\n")
    _, _, tail = rest.partition("\n====")
    path = directory / name
    path.write_text(head + "CONVERSATION\n------------\n" + "\n".join(turns) + "\n====" + tail)
    return path


def test_sections_and_header_are_parsed():
    sections = split_sections((FIXTURES / "task_2.txt").read_text())

    assert set(sections) == {"HEADER", "SCENARIO", "CRITERIA", "POLICY", "CONVERSATION", "FINAL", "JUDGE"}
    assert section_body(sections["FINAL"]) == "I could not find that order, so I cannot exchange it."
    assert section_body(sections["SCENARIO"]).startswith("domain: retail\n")
    assert parse_header(sections["HEADER"]) == {
        "reward": 0.0,
        "breakdown": "{'DB': 0.0, 'NL_ASSERTION': 0.0}",
        "termination_reason": "user_stop",
        "messages": "7",
        "model": "example-model",
    }


def test_a_rule_inside_a_turn_does_not_end_the_conversation(tau_dir: Path):
    # Only a rule followed by a section title starts a section; any other ===== line is turn text.
    turns = ["[USER]", "Where is my order?", "[ASSISTANT]", "Your order:", "==========", "#W0000001 has shipped.", "[USER]", "thanks"]
    path = task_file(tau_dir, "task_9.txt", turns)

    messages = TauRetailAdapter().record(path).messages
    assert messages[-2]["content"] == "AGENT MESSAGE:\nYour order:\n==========\n#W0000001 has shipped."
    assert messages[-1]["content"] == "USER MESSAGE:\nthanks"
    assert set(split_sections(path.read_text())) == {"HEADER", "SCENARIO", "CRITERIA", "POLICY", "CONVERSATION", "FINAL", "JUDGE"}


@pytest.mark.parametrize(
    "line", ["FINAL ANSWER: refund issued", "JUDGE the size yourself", "USER SCENARIO recap", "CONVERSATION summary", "TASK 2 is done"]
)
def test_a_rule_inside_a_turn_before_a_title_like_line_is_never_silent(tau_dir: Path, caplog: pytest.LogCaptureFixture, line: str):
    # Such a rule still splits the file, so the section appears twice: the task is skipped with a reason
    # instead of losing its later turns, replacing its conversation or losing its reward.
    turns = ["[USER]", "hi", "[ASSISTANT]", "Summary", "==========", line, "more agent text", "[USER]", "thanks", "[ASSISTANT]", "bye"]
    path = task_file(tau_dir, "task_9.txt", turns)
    split_file(tau_dir, "task_3.txt", "train")
    path.write_text(path.read_text().replace("trial=0", "trial=0  split=train", 1))

    for adapter in (TauRetailAdapter(), TauRetailAdapter(splits=frozenset({"train"}))):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert [record.batch.batch_id for record in adapter.records(tau_dir)][-1] == "task_3"
        assert "task_9.txt: section " in caplog.text and "appears twice" in caplog.text


def test_json_shaped_judge_lines_become_failed_checks():
    judge = '{"action_match": false, "action": "cancel_pending_order"}\n  "met": false, "nl": "confirms first"\n  {"met": true}'

    assert judge_outcome(0.0, judge).failed_checks == (
        'expected action not matched: { "action": "cancel_pending_order"}',
        'assertion not met: "nl": "confirms first"',
    )
    assert judge_outcome(0.0, "{'action_match': False, 'action': 'x'}").failed_checks == ("expected action not matched: { 'action': 'x'}",)


def test_pretty_printed_judge_checks_are_named_from_their_object():
    # json.dumps(..., indent=2) of tau2's action_checks / nl_assertions puts each flag on its own line.
    reward_info = {
        "action_checks": [
            {"action": {"action_id": "a0", "name": "get_order_details", "arguments": {"order_id": "#W1"}}, "action_match": True},
            {
                "action": {"action_id": "a1", "name": "exchange_delivered_order_items", "arguments": {"order_id": "#W1"}},
                "action_match": False,
            },
        ],
        "nl_assertions": [{"nl_assertion": "The agent confirms the new size.", "met": False, "justification": "it did not {sic"}],
    }
    expected = (
        'expected action not matched: exchange_delivered_order_items({"order_id": "#W1"})',
        "assertion not met: The agent confirms the new size.",
    )

    # A brace inside a string does not confuse JSON.
    assert judge_outcome(0.0, "reward=0.0\n" + json.dumps(reward_info, indent=2)).failed_checks == expected
    # The same as a pretty-printed Python repr.
    python_repr = json.dumps(reward_info, indent=2).replace(" {sic", "").replace("true", "True").replace("false", "False")
    assert judge_outcome(0.0, python_repr).failed_checks == expected
    # A flag alone on a line with no object around it still counts, under its own text.
    assert judge_outcome(0.0, "met: false").failed_checks == ("assertion not met: met: false",)


def test_conversation_turns_and_tool_io():
    turns = parse_conversation(
        conversation(
            "[USER] @2025-01-01T00:00:00",
            "hello",
            "[ASSISTANT] on the marker line",
            "  -> CALL lookup(...)",
            '     args: {"id": 1}',
            '  <- RESULT (ok) {"found": true}',
            "  -> CALL ping()",
            "  <- RESULT",
            "     multi",
            "     line",
        )
    )

    assert turns == [
        Turn("user", "hello"),
        Turn("agent", "on the marker line"),
        Turn("call", '{"id": 1}', "lookup"),
        Turn("result", '(ok) {"found": true}'),
        Turn("call", "", "ping"),
        Turn("result", "multi\n     line"),
    ]


def test_messages_rebuild_the_conversation_with_tool_calls():
    record = records()["task_1"]
    messages = record.messages

    assert messages[0] == {"role": "user", "content": "Hi, where is my latest order?"}
    assert all(message["role"] == "assistant" for message in messages[1:])
    assert messages[1]["content"] == "AGENT MESSAGE:\nI can help with that. Could you give me your name and zip code?"
    assert messages[2]["content"] == "USER MESSAGE:\nAda Example, zip 00000."
    calls = [message["content"][0] for message in messages if isinstance(message["content"], list)]
    assert [call["function"]["name"] for call in calls] == ["find_user_id_by_name_zip", "get_order_details"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"first_name": "Ada", "last_name": "Example", "zip": "00000"}
    assert messages[4]["content"] == "OBSERVATION:\n(ok)\n     ada_example_0000"
    assert messages[-1]["content"] == "AGENT MESSAGE:\nYour order #W0000001 has shipped."
    # The final answer and the ground truth are not injected as messages.
    assert not any("GROUND-TRUTH" in str(message["content"]) or "FINAL ANSWER" in str(message["content"]) for message in messages)

    # The library parser sees the agent's first input as the task and every call as an action.
    parsed = parse_openai_agents_trajectory(messages, context_messages=record.context_messages)
    assert parsed["task_instruction"] == messages[0]["content"]
    assert [call["name"] for call in parsed["function_calls"]] == ["find_user_id_by_name_zip", "get_order_details"]
    assert parsed["num_steps"] == len(messages) - 1


def test_the_agents_first_user_turn_is_the_task_not_the_hidden_scenario():
    record = records()["task_1"]
    parsed = parse_openai_agents_trajectory(record.messages, context_messages=record.context_messages)

    assert parsed["task_instruction"] == "Hi, where is my latest order?"
    # The simulator's instructions (reason_for_call, known_info, ...) never reach the extractor.
    assert not any("known_info" in str(message["content"]) or "reason_for_call" in str(message["content"]) for message in record.messages)
    assert "Hi, where is my latest order?" not in [step.split(":\n", 1)[-1] for step in parsed["steps_list"]]


def test_turns_before_the_first_user_turn_stay_in_order(tau_dir: Path):
    turns = ["[ASSISTANT]", "Hi! How can I help?", "[USER]", "Where is my order?", "[ASSISTANT]", "It shipped."]
    messages = TauRetailAdapter().record(task_file(tau_dir, "task_9.txt", turns)).messages

    assert messages == [
        {"role": "assistant", "content": "AGENT MESSAGE:\nHi! How can I help?"},
        {"role": "user", "content": "Where is my order?"},
        {"role": "assistant", "content": "AGENT MESSAGE:\nIt shipped."},
    ]
    assert parse_openai_agents_trajectory(messages)["task_instruction"] == "Where is my order?"


def test_arguments_that_are_not_a_json_object_are_kept_raw():
    calls = [m["content"][0]["function"] for m in records()["task_2"].messages if isinstance(m["content"], list)]
    assert [json.loads(call["arguments"]) for call in calls] == [{"email": "bo@example.com"}, {"_raw": "order #W0000002"}]


def test_outcome_from_reward_and_judge():
    tasks = records()
    success, failure = tasks["task_1"].outcome, tasks["task_2"].outcome

    assert success is not None and success.success and success.failed_checks == ()
    assert failure is not None and not failure.success
    assert failure.failed_checks == (
        'expected action not matched: exchange_delivered_order_items({"order_id": "#W0000002"})',
        "assertion not met: The agent confirms the new size before exchanging.",
    )
    assert failure.detail is not None and failure.detail.startswith("reward=0.0\ndb_check: db_match=False")
    assert (tasks["task_2"].model, tasks["task_1"].model) == ("example-model", None)
    assert tasks["task_1"].metadata == {
        "reward": 1.0,
        "breakdown": "{'DB': 1.0, 'COMMUNICATE': 1.0}",
        "termination_reason": "user_stop",
        "duration": "12.5s",
        "messages": "9",
        "trial": "0",
    }


def test_partial_reward_is_a_failure_and_detail_is_truncated(tau_dir: Path):
    path = tau_dir / "task_1.txt"
    path.write_text(path.read_text().replace("reward=1.0  breakdown", "reward=0.5  breakdown") + "x" * 5000)

    outcome = records(tau_dir)["task_1"].outcome
    assert outcome is not None and not outcome.success
    assert outcome.detail is not None and len(outcome.detail) == 2000


def test_malformed_files_are_skipped_with_a_warning(tau_dir: Path, caplog: pytest.LogCaptureFixture):
    text = (tau_dir / "task_1.txt").read_text()
    (tau_dir / "task_3.txt").write_text(text.replace("TASK 1  reward=1.0", "TASK 1  score=1.0"))
    (tau_dir / "task_4.txt").write_text(text.split("CONVERSATION")[0])
    (tau_dir / "task_5.txt").write_text("not a trajectory")
    (tau_dir / "task_6.txt").write_text(
        text.replace("[USER]", "").replace("[ASSISTANT]", "").replace("-> CALL", "").replace("<- RESULT", "")
    )
    task_file(tau_dir, "task_7.txt", ["[ASSISTANT]", "Hello?"])

    with caplog.at_level(logging.WARNING):
        assert list(records(tau_dir)) == ["task_1", "task_2"]

    assert [record.getMessage().split(": ", 1)[1] for record in caplog.records if record.getMessage().startswith("skipping ")] == [
        "TASK header has no reward",
        "missing section(s): CONVERSATION",
        "missing section(s): HEADER, CONVERSATION",
        "conversation has no turns",
        "conversation has no user turn",
    ]


def test_missing_or_empty_input_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="no such file"):
        list(TauRetailAdapter().records(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="no task_"):
        list(TauRetailAdapter().records(tmp_path))


def test_records_stream_in_task_order_and_a_single_file_is_accepted(tau_dir: Path):
    task_file(tau_dir, "task_10.txt", ["[USER]", "hello"])
    stream = TauRetailAdapter().records(tau_dir)

    assert isinstance(stream, Iterator)
    assert [record.trace_id for record in stream] == ["task_1", "task_2", "task_10"]
    assert list(records(tau_dir / "task_2.txt")) == ["task_2"]


def test_batch_identity_is_stable_and_revision_tracks_content(tau_dir: Path):
    first, again = records(tau_dir)["task_2"].batch, records(tau_dir)["task_2"].batch

    assert first == again
    assert (first.source, first.conversation_id, first.batch_id) == ("tau-retail", "task_2", "task_2")
    path = tau_dir / "task_2.txt"
    path.write_text(path.read_text().replace("reward=0.0  breakdown", "reward=1.0  breakdown"))
    corrected = records(tau_dir)["task_2"].batch
    assert corrected.batch_id == first.batch_id and corrected.revision != first.revision
    # An option that changes what is processed is a new revision too.
    assert records(tau_dir, include_policy=True)["task_2"].batch.revision != corrected.revision
    assert records(tau_dir, include_scenario=True)["task_2"].batch.revision != corrected.revision
    assert records(tau_dir)["task_1"].batch.revision == records(FIXTURES)["task_1"].batch.revision


def test_long_conversations_pass_through_whole(tau_dir: Path):
    turns = [line for i in range(40) for line in ("[USER]", f"question {i}", "[ASSISTANT]", f"answer {i}")]
    messages = TauRetailAdapter().record(task_file(tau_dir, "task_9.txt", turns)).messages

    # The task, then the other 79 turns; the adapter no longer elides.
    assert len(messages) == 1 + 79
    assert messages[0] == {"role": "user", "content": "question 0"}
    assert messages[1]["content"] == "AGENT MESSAGE:\nanswer 0"
    assert messages[-1]["content"] == "AGENT MESSAGE:\nanswer 39"
    # A window with a tail keeps the ending; the default head-only one does not.
    assert parse_openai_agents_trajectory(messages, options=GuidelineRuntime(trajectory_tail_steps=24))["steps_list"][-1].endswith(
        "answer 39"
    )
    assert not parse_openai_agents_trajectory(messages, options=GuidelineRuntime())["steps_list"][-1].endswith("answer 39")


def test_long_turns_are_split_so_per_step_truncation_drops_nothing(tau_dir: Path):
    body = "".join(f"{i:05d}" for i in range(1000))  # 5000 chars
    messages = TauRetailAdapter().record(task_file(tau_dir, "task_9.txt", ["[USER]", "hi", "[ASSISTANT]", body])).messages

    parts = [message["content"] for message in messages[1:]]
    assert len(parts) == 3 and all(len(part) <= 2000 for part in parts)
    assert parts[0].startswith("AGENT MESSAGE (part 1/3):\n")
    assert "".join(part.split(":\n", 1)[1] for part in parts) == body


POLICY = (
    "AGENT DOMAIN POLICY:\n# Retail agent policy (synthetic)\n"
    "Authenticate the user by email, or by name and zip code, before acting.\n"
    "Only act on orders that belong to the authenticated user."
)


def test_policy_and_scenario_context_are_opt_in(tau_dir: Path):
    assert records()["task_1"].context_messages == []
    assert records(include_policy=True)["task_1"].context_messages == [{"role": "system", "content": POLICY}]

    record = records(include_policy=True, include_scenario=True)["task_1"]
    scenario = section_body(split_sections((FIXTURES / "task_1.txt").read_text())["SCENARIO"])
    assert record.context_messages == [
        {"role": "system", "content": POLICY},
        {"role": "system", "content": f"USER SCENARIO (the user simulator's brief; the agent did not see it):\n{scenario}"},
    ]
    # Context is never mistaken for the task.
    parsed = parse_openai_agents_trajectory(record.messages, context_messages=record.context_messages)
    assert parsed["task_instruction"] == "Hi, where is my latest order?"

    path = tau_dir / "task_1.txt"
    path.write_text(path.read_text().replace("Only act", "Only act " + "x" * 4000))
    long_policy = records(tau_dir, include_policy=True)["task_1"].context_messages
    assert len(long_policy) == 3 and long_policy[0]["content"].startswith("AGENT DOMAIN POLICY (part 1/3):\n")


def test_adapter_options_configure_a_copy():
    adapter = get_adapter("tau-retail", {"include_policy": "true", "include_scenario": "yes", "splits": "train, dev"})

    assert isinstance(adapter, TauRetailAdapter) and adapter is not ADAPTERS["tau-retail"]
    assert (adapter.include_policy, adapter.include_scenario, adapter.splits) == (True, True, frozenset({"train", "dev"}))
    assert ADAPTERS["tau-retail"] == TauRetailAdapter()
    with pytest.raises(ValueError, match="Unknown tau-retail option 'policy'"):
        get_adapter("tau-retail", {"policy": "true"})
    # A typo is an error, not a silent false (bool("False") is True, and "ture" is not false either).
    for value in ("maybe", "ture", "2"):
        with pytest.raises(ValueError, match="tau-retail option include_policy must be true or false"):
            get_adapter("tau-retail", {"include_policy": value})
    # An empty filter (say, an unset shell variable) would mean "no filter", so it is refused.
    for empty in ("", " , "):
        with pytest.raises(ValueError, match="tau-retail option splits names nothing"):
            get_adapter("tau-retail", {"splits": empty})


def split_file(directory: Path, name: str, split: str | None) -> Path:
    path = task_file(directory, name, ["[USER]", f"question for {name}"])
    if split is not None:
        path.write_text(path.read_text().replace("trial=0", f"trial=0  split={split}", 1))
    return path


def test_split_is_recorded_and_selects_tasks(tau_dir: Path, caplog: pytest.LogCaptureFixture):
    split_file(tau_dir, "task_3.txt", "train")
    split_file(tau_dir, "task_4.txt", "test")

    tasks = records(tau_dir)
    assert (tasks["train/task_3"].metadata["split"], "split" in tasks["task_1"].metadata) == ("train", False)
    # Splits can reuse task numbers, so a known split is part of the task's identity: the trace_id
    # (each guideline's source_task_id) and the conversation alike.
    assert (tasks["train/task_3"].batch.conversation_id, tasks["task_1"].batch.conversation_id) == ("train/task_3", "task_1")
    assert tasks["train/task_3"].batch.batch_id == "task_3"
    assert sorted(tasks) == ["task_1", "task_2", "train/task_3"]
    assert "skipped test-split tasks" in caplog.text
    assert list(records(tau_dir, splits=frozenset({"test"}))) == ["test/task_4"]

    with caplog.at_level(logging.WARNING):
        assert list(records(tau_dir, splits=frozenset({"train"}))) == ["train/task_3"]
    assert "records no split" in caplog.text and "task_1.txt" in caplog.text and "task_2.txt" in caplog.text

    with pytest.raises(ValueError, match=r"Splits not found in .*\['dev'\] \(found \['test', 'train'\]\)"):
        next(TauRetailAdapter(splits=frozenset({"dev", "train"})).records(tau_dir))


def test_split_file_selects_tasks_by_number(tau_dir: Path, tmp_path: Path):
    # tau2's split_tasks.json lists task IDs over the base set, so one run directory holds both
    # splits and its headers can record at most split=base.
    for number in (0, 3, 4, 10):
        split_file(tau_dir, f"task_{number}.txt", "base")
    table = tmp_path / "split_tasks.json"
    table.write_text(json.dumps({"train": ["0", "2", "4"], "test": ["1", "3", "10"], "base": [str(n) for n in range(11)]}))

    def mined(splits: str, path: Path = tau_dir) -> list[str]:
        adapter = get_adapter("tau-retail", {"split_file": str(table), "splits": splits})
        return [record.batch.batch_id for record in adapter.records(path)]

    assert mined("train") == ["task_0", "task_2", "task_4"]
    assert mined("test") == ["task_1", "task_3", "task_10"]
    assert mined("train,test") == mined("base") == ["task_0", "task_1", "task_2", "task_3", "task_4", "task_10"]
    assert mined("test", tau_dir / "task_2.txt") == []
    adapter = get_adapter("tau-retail", {"split_file": str(table)})
    assert [r.batch.batch_id for r in adapter.records(tau_dir)] == ["task_0", "task_2", "task_4"]
    # Numbers match without leading zeros, and IDs may be JSON numbers.
    table.write_text(json.dumps({"train": [0, "002"]}))
    assert mined("train") == ["task_0", "task_2"]

    assert len(list(get_adapter("tau-retail", {"split_file": str(table)}).records(tau_dir))) == 6
    with pytest.raises(ValueError, match=r"Splits not found in tau-retail split file .*\['dev'\] \(found \['train'\]\)"):
        get_adapter("tau-retail", {"splits": "dev,train", "split_file": str(table)})
    for bad in ('["0", "1"]', '{"train": "0"}', '{"train": [true]}', "{}", "not json"):
        table.write_text(bad)
        with pytest.raises(ValueError, match="split file"):
            get_adapter("tau-retail", {"split_file": str(table), "splits": "train"})
    with pytest.raises(ValueError, match="Cannot read tau-retail split file"):
        get_adapter("tau-retail", {"split_file": str(tmp_path / "missing.json"), "splits": "train"})


def test_reading_no_task_is_never_silent(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    (tmp_path / "task_1.txt").write_text("not a trajectory")
    with caplog.at_level(logging.WARNING):
        assert list(TauRetailAdapter().records(tmp_path)) == []
    assert "read no tau-retail task" in caplog.text

    # Task files one level down (a run directory instead of its trajectories/) are named in the error.
    nested = tmp_path / "run" / "trajectories"
    nested.mkdir(parents=True)
    shutil.copy(FIXTURES / "task_1.txt", nested)
    with pytest.raises(FileNotFoundError, match=r"no task_\*\.txt files in .*run; found some in trajectories"):
        list(TauRetailAdapter().records(tmp_path / "run"))


def test_registered_and_mined_end_to_end(client: EvolveClient, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    assert isinstance(get_adapter("tau-retail"), TauRetailAdapter)
    monkeypatch.setattr(cli, "make_client", lambda: pytest.fail("dry run must not touch storage"))
    argv = ["mine", "--adapter", "tau-retail", "--input", str(FIXTURES), "--namespace", "memories", "--processing-profile", "echo"]

    assert cli.main([*argv, "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "dry run: 2 valid, 0 failed"
    assert cli.main([*argv, "--dry-run", "--adapter-option", "include_policy=true"]) == 0
    assert capsys.readouterr().out.strip() == "dry run: 2 valid, 0 failed"
    assert cli.main([*argv, "--dry-run", "--adapter-option", "include_policy=maybe"]) == 2
    assert "tau-retail option include_policy must be true or false" in capsys.readouterr().err
    assert cli.main([*argv, "--dry-run", "--adapter-option", "splits="]) == 2
    assert "tau-retail option splits names nothing" in capsys.readouterr().err

    # Through a profile, the outcome becomes evidence and a re-run skips both checkpointed tasks.
    report = mine(ADAPTERS["tau-retail"].records(FIXTURES), client, namespace_id="memories", processing_profile="echo")
    assert (report.processed, report.failures) == (2, [])
    stored = sorted(client.get_all_entities("memories"), key=lambda entity: str(entity.content))
    assert [entity.metadata["success"] for entity in stored] == [True, False]
    again = mine(ADAPTERS["tau-retail"].records(FIXTURES), client, namespace_id="memories", processing_profile="echo")
    assert (again.processed, again.skipped) == (0, 2)


def test_default_split_warning_is_once_per_run(tau_dir, caplog):
    split_file(tau_dir, "task_3.txt", "test_normal")
    split_file(tau_dir, "task_4.txt", "test")
    with caplog.at_level(logging.WARNING):
        assert sorted(records(tau_dir)) == ["task_1", "task_2"]
    warnings = [r.getMessage() for r in caplog.records if "skipped test-split tasks" in r.getMessage()]
    assert len(warnings) == 1
    assert "2; kept 2 tasks with unknown split" in warnings[0]
    assert list(records(tau_dir, splits=frozenset({"test_normal"}))) == ["test_normal/task_3"]


@pytest.mark.parametrize("label", [None, "base", "dev"])
def test_default_warns_for_unknown_labels_only_after_valid_records(tau_dir, caplog, label):
    for number in (1, 2):
        split_file(tau_dir, f"task_{number}.txt", label)
    (tau_dir / "task_3.txt").write_text("not a trajectory")
    (tau_dir / "task_4.txt").write_text("TASK 4 trial=0 reward=1\n")
    assert len(records(tau_dir)) == 2
    summaries = [r.getMessage() for r in caplog.records if "unknown split" in r.getMessage()]
    assert summaries == [f"kept 2 tasks with unknown split in {tau_dir}; use split_file= to classify base or unknown tasks"]
    assert "skipped test-split tasks" not in caplog.text


@pytest.mark.parametrize("with_table", [False, True])
def test_all_train_run_has_no_warning(tau_dir, tmp_path, caplog, with_table):
    for number in (1, 2):
        split_file(tau_dir, f"task_{number}.txt", "train")
    options = {}
    if with_table:
        table = tmp_path / "splits.json"
        table.write_text(json.dumps({"train": [1, 2]}))
        options["split_file"] = str(table)
    assert len(list(get_adapter("tau-retail", options).records(tau_dir))) == 2
    assert not caplog.records


@pytest.mark.parametrize("header", ["test", "test_normal", "Test", "TEST_NORMAL"])
@pytest.mark.parametrize("listed", [True, False])
def test_either_source_excludes_test_tasks(tau_dir, tmp_path, caplog, header, listed):
    split_file(tau_dir, "task_1.txt", header)
    split_file(tau_dir, "task_2.txt", "train")
    split_file(tau_dir, "task_3.txt", "train")
    split_file(tau_dir, "task_4.txt", "base")
    split_file(tau_dir, "task_5.txt", None)
    table = tmp_path / "splits.json"
    table.write_text(json.dumps({"train": [1, 2] if listed else [2], "Test_Normal": ["003"], "base": [4]}))
    adapter = get_adapter("tau-retail", {"split_file": str(table)})
    assert [r.batch.batch_id for r in adapter.records(tau_dir)] == ["task_2", "task_4", "task_5"]
    assert [r.getMessage() for r in caplog.records] == [
        f"skipped test-split tasks in {tau_dir}: 2; kept 2 tasks with unknown split; "
        "use splits= to opt in to test splits; use split_file= to classify base or unknown tasks"
    ]
    # Explicit filters still use the requested source, including conflicting metadata.
    opted_in = get_adapter("tau-retail", {"split_file": str(table), "splits": "Test_Normal"})
    assert [r.batch.batch_id for r in opted_in.records(tau_dir)] == ["task_3"]
    assert list(records(tau_dir, splits=frozenset({header}))) == [f"{header}/task_1"]


@pytest.mark.parametrize("header", ["Test", "TEST_NORMAL"])
def test_default_excludes_case_insensitive_header_without_table(tau_dir, header):
    split_file(tau_dir, "task_1.txt", header)
    assert list(records(tau_dir)) == ["task_2"]


def test_direct_split_table_construction_and_reconfiguration(tau_dir: Path, tmp_path: Path):
    table = tmp_path / "split_tasks.json"
    table.write_text(json.dumps({"train": ["2"], "test_normal": ["1"]}))
    adapter = TauRetailAdapter(split_file=table)
    assert [r.batch.batch_id for r in adapter.records(tau_dir)] == ["task_2"]
    selected = TauRetailAdapter(split_file=table, splits=frozenset({"test_normal"}))
    assert [r.batch.batch_id for r in selected.records(tau_dir)] == ["task_1"]
    changed = adapter.configure({"splits": "test_normal"})
    assert [r.batch.batch_id for r in changed.records(tau_dir)] == ["task_1"]
    assert [r.batch.batch_id for r in adapter.records(tau_dir)] == ["task_2"]
    with pytest.raises(ValueError, match="Splits not found"):
        adapter.configure({"splits": "missing"})
    with pytest.raises(ValueError, match="Cannot read"):
        TauRetailAdapter(split_file=tmp_path / "missing.json")
