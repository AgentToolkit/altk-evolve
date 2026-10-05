"""The tau-retail adapter, on hand-written synthetic task files: no LLM, no network."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.llm.guidelines.guidelines import parse_openai_agents_trajectory

from experiments.guideline_pipeline import __main__ as cli
from experiments.guideline_pipeline.adapters import ADAPTERS, get_adapter
from experiments.guideline_pipeline.adapters.tau import (
    INCLUDE_POLICY_ENV,
    PARSER_STEP_LIMIT,
    TauRetailAdapter,
    Turn,
    elide_head_tail,
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

    assert messages[0] == {"role": "user", "content": section_body(split_sections((FIXTURES / "task_1.txt").read_text())["SCENARIO"])}
    assert all(message["role"] == "assistant" for message in messages[1:])
    assert messages[1]["content"] == "USER MESSAGE:\nHi, where is my latest order?"
    calls = [message["content"][0] for message in messages if isinstance(message["content"], list)]
    assert [call["function"]["name"] for call in calls] == ["find_user_id_by_name_zip", "get_order_details"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"first_name": "Ada", "last_name": "Example", "zip": "00000"}
    assert messages[5]["content"] == "OBSERVATION:\n(ok)\n     ada_example_0000"
    assert messages[-1]["content"] == "AGENT MESSAGE:\nYour order #W0000001 has shipped."
    # The final answer and the ground truth are not injected as messages.
    assert not any("GROUND-TRUTH" in str(message["content"]) or "FINAL ANSWER" in str(message["content"]) for message in messages)

    # The library parser sees the scenario as the task and every call as an action.
    parsed = parse_openai_agents_trajectory(messages, context_messages=record.context_messages)
    assert parsed["task_instruction"] == messages[0]["content"]
    assert [call["name"] for call in parsed["function_calls"]] == ["find_user_id_by_name_zip", "get_order_details"]
    assert parsed["num_steps"] == len(messages) - 1


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

    with caplog.at_level(logging.WARNING):
        assert list(records(tau_dir)) == ["task_1", "task_2"]

    assert [record.getMessage().split(": ", 1)[1] for record in caplog.records] == [
        "TASK header has no reward",
        "missing section(s): CONVERSATION",
        "missing section(s): HEADER, SCENARIO, CONVERSATION",
        "conversation has no turns",
    ]


def test_missing_or_empty_input_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="no such file"):
        list(TauRetailAdapter().records(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="no task_"):
        list(TauRetailAdapter().records(tmp_path))


def test_records_stream_in_task_order_and_a_single_file_is_accepted(tau_dir: Path):
    task_file(tau_dir, "task_10.txt", ["[ASSISTANT]", "hello"])
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
    # Including the policy changes what is processed, so it is a new revision too.
    assert records(tau_dir, include_policy=True)["task_2"].batch.revision != corrected.revision
    assert records(tau_dir)["task_1"].batch.revision == records(FIXTURES)["task_1"].batch.revision


def test_long_conversations_keep_their_head_and_tail(tau_dir: Path):
    steps = [{"role": "assistant", "content": f"step {i}"} for i in range(10)]
    assert elide_head_tail(steps, limit=10) == steps
    elided = elide_head_tail(steps, limit=5)
    assert [step["content"] for step in elided] == ["step 0", "step 1", "... (6 conversation steps elided) ...", "step 8", "step 9"]

    turns = [line for i in range(40) for line in ("[USER]", f"question {i}", "[ASSISTANT]", f"answer {i}")]
    messages = TauRetailAdapter().record(task_file(tau_dir, "task_9.txt", turns)).messages
    assert len(messages) == 1 + PARSER_STEP_LIMIT
    assert messages[1]["content"] == "USER MESSAGE:\nquestion 0"
    assert messages[26]["content"] == "... (31 conversation steps elided) ..."
    assert messages[-1]["content"] == "AGENT MESSAGE:\nanswer 39"
    # The ending now survives the parser's step cap.
    assert parse_openai_agents_trajectory(messages)["steps_list"][-1].endswith("answer 39")


def test_long_turns_are_split_so_per_step_truncation_drops_nothing(tau_dir: Path):
    body = "".join(f"{i:05d}" for i in range(1000))  # 5000 chars
    messages = TauRetailAdapter().record(task_file(tau_dir, "task_9.txt", ["[ASSISTANT]", body])).messages

    parts = [message["content"] for message in messages[1:]]
    assert len(parts) == 3 and all(len(part) <= 2000 for part in parts)
    assert parts[0].startswith("AGENT MESSAGE (part 1/3):\n")
    assert "".join(part.split(":\n", 1)[1] for part in parts) == body


def test_policy_context_is_opt_in(monkeypatch: pytest.MonkeyPatch, tau_dir: Path):
    monkeypatch.delenv(INCLUDE_POLICY_ENV, raising=False)
    assert records()["task_1"].context_messages == []

    context = records(include_policy=True)["task_1"].context_messages
    assert context == [
        {
            "role": "system",
            "content": "AGENT DOMAIN POLICY:\n# Retail agent policy (synthetic)\n"
            "Authenticate the user by email, or by name and zip code, before acting.\n"
            "Only act on orders that belong to the authenticated user.",
        }
    ]
    # Context is never mistaken for the task, and the CLI opts in through the environment.
    record = records(include_policy=True)["task_1"]
    assert parse_openai_agents_trajectory(record.messages, context_messages=record.context_messages)["task_instruction"].startswith(
        "domain:"
    )
    monkeypatch.setenv(INCLUDE_POLICY_ENV, "1")
    assert records()["task_1"].context_messages == context
    assert records(include_policy=False)["task_1"].context_messages == []

    path = tau_dir / "task_1.txt"
    path.write_text(path.read_text().replace("Only act", "Only act " + "x" * 4000))
    long_policy = records(tau_dir)["task_1"].context_messages
    assert len(long_policy) == 3 and long_policy[0]["content"].startswith("AGENT DOMAIN POLICY (part 1/3):\n")


def test_registered_and_mined_end_to_end(client: EvolveClient, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    assert isinstance(get_adapter("tau-retail"), TauRetailAdapter)
    monkeypatch.setattr(cli, "make_client", lambda: pytest.fail("dry run must not touch storage"))
    argv = ["mine", "--adapter", "tau-retail", "--input", str(FIXTURES), "--namespace", "memories", "--processing-profile", "echo"]

    assert cli.main([*argv, "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "dry run: 2 valid, 0 failed"

    # Through a profile, the outcome becomes evidence and a re-run skips both checkpointed tasks.
    report = mine(ADAPTERS["tau-retail"].records(FIXTURES), client, namespace_id="memories", processing_profile="echo")
    assert (report.processed, report.failures) == (2, [])
    stored = sorted(client.get_all_entities("memories"), key=lambda entity: str(entity.content))
    assert [entity.metadata["success"] for entity in stored] == [True, False]
    again = mine(ADAPTERS["tau-retail"].records(FIXTURES), client, namespace_id="memories", processing_profile="echo")
    assert (again.processed, again.skipped) == (0, 2)
