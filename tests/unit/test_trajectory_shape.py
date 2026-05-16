"""Tests for altk_evolve.llm.outcome_extraction.trajectory_shape (Phase 2)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from altk_evolve.llm.outcome_extraction.trajectory_shape import (
    extract_trajectory_shape_signals,
)
from altk_evolve.schema.outcome_evidence import OutcomeKind, SignalSource


pytestmark = pytest.mark.unit


def _ts() -> datetime:
    return datetime(2026, 5, 15, 14, 0, 0, tzinfo=timezone.utc)


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str = "", *, tool_calls: list | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_result(text: str) -> dict:
    return {"role": "tool", "tool_call_id": "tc-1", "content": text}


def _extract(messages: list[dict]) -> list:
    return extract_trajectory_shape_signals(messages, trajectory_id="t1", observed_at=_ts())


# ── max-iter detection ─────────────────────────────────────────────────────


class TestMaxIter:
    def test_explicit_max_iterations(self) -> None:
        messages = [_user("solve it"), _assistant("Maximum iterations reached, aborting.")]
        observations = _extract(messages)
        max_iter = [o for o in observations if "max-iter" in (o.detail or "").lower() or "context-limit" in (o.detail or "").lower()]
        assert len(max_iter) == 1
        assert max_iter[0].observed_outcome is OutcomeKind.FAILURE
        assert max_iter[0].signal_source is SignalSource.TRAJECTORY_SHAPE
        assert max_iter[0].confidence == 0.7

    def test_max_iter_alternate_phrasings(self) -> None:
        for phrase in [
            "max iterations exceeded",
            "context length exceeded",
            "context window exceeded",
            "Took too long, giving up",
            "step limit reached",
            "recursion limit hit",
            "too many iterations have passed",
        ]:
            messages = [_user("x"), _assistant(phrase)]
            observations = _extract(messages)
            assert any(o.observed_outcome is OutcomeKind.FAILURE for o in observations), phrase

    def test_max_iter_anywhere_in_trajectory(self) -> None:
        # Even if max-iter mentioned in middle, it counts.
        messages = [
            _user("x"),
            _assistant("trying..."),
            _assistant("we hit context window exceeded so I'll summarize"),
            _assistant("here is a substantial summary that should not normally trigger anything"),
        ]
        observations = _extract(messages)
        # We expect a max-iter failure observation.
        assert any(o.observed_outcome is OutcomeKind.FAILURE for o in observations)

    def test_only_one_max_iter_observation_per_trajectory(self) -> None:
        # Multiple mentions should still emit only one observation.
        messages = [
            _user("x"),
            _assistant("max iterations reached"),
            _assistant("max iterations reached again"),
        ]
        observations = _extract(messages)
        max_iter_obs = [o for o in observations if (o.detail or "").startswith("max-iteration")]
        assert len(max_iter_obs) == 1


# ── clean terminate detection ──────────────────────────────────────────────


class TestCleanTerminate:
    def test_trajectory_ends_with_substantive_assistant_answer(self) -> None:
        messages = [
            _user("What's 2+2?"),
            _assistant("It's 4. Let me explain: addition is the sum of two integers; in this case the result is four."),
        ]
        observations = _extract(messages)
        success = [o for o in observations if o.observed_outcome is OutcomeKind.SUCCESS]
        assert len(success) == 1
        assert success[0].confidence == 0.65
        assert success[0].signal_source is SignalSource.TRAJECTORY_SHAPE

    def test_short_trailing_assistant_message_does_not_count(self) -> None:
        messages = [_user("yes?"), _assistant("ok")]  # 2 chars — way below threshold
        observations = _extract(messages)
        assert observations == []

    def test_trajectory_ending_on_user_message_yields_no_signal(self) -> None:
        messages = [
            _assistant("Here's a substantive answer that exceeds the threshold for being interesting."),
            _user("thanks"),
        ]
        observations = _extract(messages)
        # No signal — user spoke last, agent didn't deliver the trailing answer.
        assert observations == []

    def test_trajectory_ending_on_assistant_tool_call_yields_no_signal(self) -> None:
        # Mid-tool-call capture: assistant invoked a tool but no result yet.
        messages = [
            _user("query"),
            _assistant("calling api...", tool_calls=[{"id": "tc-1", "function": {"name": "f"}}]),
        ]
        observations = _extract(messages)
        assert observations == []

    def test_trajectory_with_intermediate_tool_then_substantive_terminate(self) -> None:
        messages = [
            _user("get weather and explain"),
            _assistant("calling weather api", tool_calls=[{"id": "tc-1", "function": {"name": "weather"}}]),
            _tool_result("75F sunny"),
            _assistant("It is currently 75 degrees and sunny — a great day for a walk in the park."),
        ]
        observations = _extract(messages)
        success = [o for o in observations if o.observed_outcome is OutcomeKind.SUCCESS]
        assert len(success) == 1


# ── early abort detection ──────────────────────────────────────────────────


class TestEarlyAbort:
    def test_assistant_explicitly_declines(self) -> None:
        for phrase in [
            "I cannot help with this request.",
            "I can't access that information.",
            "I am unable to complete this task.",
            "I don't have access to your private data.",
        ]:
            messages = [_user("do thing"), _assistant(phrase)]
            observations = _extract(messages)
            failures = [o for o in observations if o.observed_outcome is OutcomeKind.FAILURE]
            assert len(failures) == 1, phrase
            assert failures[0].confidence == 0.65

    def test_early_abort_does_not_also_trigger_terminate(self) -> None:
        # Even if the abort message is substantial, we should NOT also emit
        # a SUCCESS clean-terminate observation.
        messages = [
            _user("do thing"),
            _assistant("I cannot help with this very long request that has many many words for length."),
        ]
        observations = _extract(messages)
        successes = [o for o in observations if o.observed_outcome is OutcomeKind.SUCCESS]
        assert successes == []


# ── invariants ─────────────────────────────────────────────────────────────


class TestInvariants:
    def test_empty_messages_returns_empty(self) -> None:
        assert _extract([]) == []

    def test_all_observations_carry_trajectory_id(self) -> None:
        messages = [_user("x"), _assistant("Here is a substantive answer that's long enough to count.")]
        observations = _extract(messages)
        assert all(o.trajectory_id == "t1" for o in observations)

    def test_observations_carry_observed_at(self) -> None:
        ts = _ts()
        messages = [_user("x"), _assistant("max iterations reached")]
        observations = extract_trajectory_shape_signals(messages, trajectory_id="t1", observed_at=ts)
        assert all(o.observed_at == ts for o in observations)


# ── dual-convention support (tool result as role=user) ────────────────────


class TestAnthropicAppworldConvention:
    """Trajectory shape detection must handle the dialect where tool results
    arrive as role=user messages, not as separate role=tool messages.

    In this dialect, "trajectory ends with a successful tool action" looks
    like: assistant(tool_calls=[X]) → user("Output: {...}"). The current
    extractor must recognize this as a clean terminate, not as mid-task."""

    def test_tool_action_terminate_with_user_output_message(self) -> None:
        messages = [
            _user("send money"),
            _assistant("", tool_calls=[{"id": "t1", "type": "function", "function": {"name": "send_money", "arguments": "{}"}}]),
            # Successful tool output as role=user (AppWorld style).
            _user('Output:\n```\n{"message": "Sent.", "id": 8216}\n```'),
        ]
        observations = _extract(messages)
        successes = [o for o in observations if o.observed_outcome is OutcomeKind.SUCCESS]
        assert len(successes) == 1
        assert successes[0].detail is not None
        assert "tool action" in successes[0].detail

    def test_tool_action_terminate_with_error_output_does_not_emit_success(self) -> None:
        messages = [
            _user("send money"),
            _assistant("", tool_calls=[{"id": "t1", "type": "function", "function": {"name": "send_money", "arguments": "{}"}}]),
            # Error tool output — clean-terminate must NOT fire.
            _user('Output:\n```\n{"error": "503 Service Unavailable"}\n```'),
        ]
        observations = _extract(messages)
        successes = [o for o in observations if o.observed_outcome is OutcomeKind.SUCCESS]
        assert successes == []

    def test_mid_task_capture_no_tool_result_yet(self) -> None:
        # Last assistant has tool_calls AND nothing follows → mid-task; no shape signal.
        messages = [
            _user("do thing"),
            _assistant("", tool_calls=[{"id": "t1", "type": "function", "function": {"name": "do", "arguments": "{}"}}]),
        ]
        observations = _extract(messages)
        # No SUCCESS / no FAILURE; mid-task is intentionally silent.
        assert all(o.observed_outcome is not OutcomeKind.SUCCESS for o in observations)


class TestVenmoFixtureRegression:
    def test_venmo_trajectory_emits_clean_terminate_success(self) -> None:
        import json
        import pathlib

        msgs = json.loads((pathlib.Path(__file__).parent.parent / "fixtures" / "appworld_venmo_task_trajectory.json").read_text())
        observations = _extract(msgs)
        successes = [o for o in observations if o.observed_outcome is OutcomeKind.SUCCESS]
        # The fixture is a successful venmo task; the extractor should detect at
        # least one terminate-style success. Exact flavor (chat-style vs tool-action)
        # is not pinned — both are valid signals on this trajectory.
        assert len(successes) >= 1
        assert successes[0].signal_source is SignalSource.TRAJECTORY_SHAPE
        assert successes[0].confidence == 0.65
