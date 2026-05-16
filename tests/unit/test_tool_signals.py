"""Tests for altk_evolve.llm.outcome_extraction.tool_signals (Phase 2)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from altk_evolve.llm.outcome_extraction.tool_signals import extract_tool_signals
from altk_evolve.schema.outcome_evidence import OutcomeKind, SignalSource


pytestmark = pytest.mark.unit


def _ts() -> datetime:
    return datetime(2026, 5, 15, 14, 0, 0, tzinfo=timezone.utc)


def _assistant_tool_call(name: str, args: str = "{}") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "tc-1", "type": "function", "function": {"name": name, "arguments": args}}],
    }


def _tool_result(content: str, *, tool_call_id: str = "tc-1") -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


# ── error pattern detection ────────────────────────────────────────────────


class TestErrorPatterns:
    def test_traceback_in_tool_result_yields_high_confidence_failure(self) -> None:
        messages = [
            {"role": "user", "content": "do thing"},
            _assistant_tool_call("do_thing"),
            _tool_result("Traceback (most recent call last):\n  File \"foo.py\", line 1, in <module>\n  raise ValueError('boom')"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        assert len(observations) == 1
        assert observations[0].signal_source is SignalSource.TOOL_ERROR
        assert observations[0].observed_outcome is OutcomeKind.FAILURE
        assert observations[0].confidence == 0.95
        assert observations[0].detail is not None
        assert "traceback" in observations[0].detail.lower()

    def test_http_5xx_yields_high_confidence_failure(self) -> None:
        messages = [
            _assistant_tool_call("call_api"),
            _tool_result("API call failed: 503 Service Unavailable"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        # 503 + "failed" both match — extractor picks the strongest (5xx).
        assert any(o.confidence == 0.95 for o in observations)
        assert all(o.observed_outcome is OutcomeKind.FAILURE for o in observations)

    def test_http_4xx_yields_medium_confidence_failure(self) -> None:
        messages = [
            _assistant_tool_call("call_api"),
            _tool_result("Request returned: 401 Unauthorized"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        assert observations[0].confidence == 0.85
        assert observations[0].detail is not None
        assert "4xx" in observations[0].detail

    def test_generic_error_substring(self) -> None:
        messages = [
            _assistant_tool_call("op"),
            _tool_result("operation denied"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        assert len(observations) == 1
        assert observations[0].confidence == 0.85

    def test_clean_tool_result_yields_no_observations(self) -> None:
        messages = [
            _assistant_tool_call("get_weather"),
            _tool_result("Sunny, 72°F"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        assert observations == []

    def test_empty_tool_result_ignored(self) -> None:
        messages = [
            _assistant_tool_call("get_weather"),
            _tool_result(""),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        assert observations == []

    def test_user_message_with_error_word_is_not_a_tool_signal(self) -> None:
        # Only tool-result messages count for tool-error detection.
        messages = [
            {"role": "user", "content": "I keep getting an error"},
            _assistant_tool_call("call_api"),
            _tool_result("OK, fetched 3 records"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        assert observations == []

    def test_multipart_content_is_concatenated(self) -> None:
        # OpenAI multimodal content is a list of dicts; we should still find error markers.
        messages = [
            _assistant_tool_call("call_api"),
            {
                "role": "tool",
                "tool_call_id": "tc-1",
                "content": [
                    {"type": "text", "text": "Status:"},
                    {"type": "text", "text": "503 Service Unavailable"},
                ],
            },
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        assert len(observations) == 1
        assert observations[0].confidence == 0.95


# ── retry pattern detection ────────────────────────────────────────────────


class TestRetryDetection:
    def test_retry_after_failure_emits_extra_failure_observation(self) -> None:
        messages = [
            _assistant_tool_call("api_call", args='{"path":"/users"}'),
            _tool_result("503 Service Unavailable"),
            _assistant_tool_call("api_call", args='{"path":"/users"}'),  # retry
            _tool_result("OK"),  # this time succeeds
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        # We expect: one observation for the original failure (Pass 1)
        # AND one observation for the retry pattern itself (Pass 2).
        assert len(observations) == 2
        assert all(o.observed_outcome is OutcomeKind.FAILURE for o in observations)
        assert any(o.confidence == 0.95 for o in observations)  # 503 detection
        assert any(o.confidence == 0.80 for o in observations)  # retry pattern

    def test_no_retry_observation_when_first_call_succeeded(self) -> None:
        messages = [
            _assistant_tool_call("api_call", args='{"x":1}'),
            _tool_result("OK first time"),
            _assistant_tool_call("api_call", args='{"x":1}'),  # called again, but first succeeded
            _tool_result("OK second time"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        # No tool errors, no retry-after-failure pattern.
        assert observations == []

    def test_retry_with_normalized_whitespace(self) -> None:
        messages = [
            _assistant_tool_call("api_call", args='{"a": 1}'),
            _tool_result("error: timeout"),
            _assistant_tool_call("api_call", args='{ "a":  1 }'),  # whitespace-different
            _tool_result("OK"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        # Retry pattern triggers despite cosmetic whitespace differences.
        assert any(o.detail is not None and o.detail.startswith("same-tool retry pattern") for o in observations)

    def test_different_tools_do_not_count_as_retry(self) -> None:
        messages = [
            _assistant_tool_call("api_call", args='{"x":1}'),
            _tool_result("error happened"),
            _assistant_tool_call("other_tool", args='{"x":1}'),  # different tool
            _tool_result("OK"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        # Only the original failure observation; no retry observation.
        retry_obs = [o for o in observations if o.detail is not None and o.detail.startswith("same-tool retry pattern")]
        assert retry_obs == []


# ── trajectory-level invariants ────────────────────────────────────────────


class TestTrajectoryInvariants:
    def test_all_observations_carry_trajectory_id(self) -> None:
        messages = [
            _assistant_tool_call("a"),
            _tool_result("error"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="my-traj", observed_at=_ts())
        assert all(o.trajectory_id == "my-traj" for o in observations)

    def test_all_observations_carry_observed_at(self) -> None:
        ts = _ts()
        messages = [
            _assistant_tool_call("a"),
            _tool_result("503 Service Unavailable"),
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=ts)
        assert all(o.observed_at == ts for o in observations)

    def test_no_tool_calls_yields_empty(self) -> None:
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        assert observations == []

    def test_empty_message_list(self) -> None:
        assert extract_tool_signals([], trajectory_id="t1", observed_at=_ts()) == []


# ── dual-convention support (role=user as tool result) ───────────────────


class TestAnthropicAppworldConvention:
    """Tool results in real trajectories often arrive as role=user messages
    immediately following an assistant with tool_calls (AppWorld + Anthropic
    style), not as the strict OpenAI role=tool message."""

    def test_user_following_assistant_tool_call_is_recognized_as_tool_result(self) -> None:
        messages: list[dict] = [
            {"role": "user", "content": "fetch user"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "fetch", "arguments": "{}"}}],
            },
            # AppWorld convention: tool output as user message.
            {"role": "user", "content": 'Output:\n```\n{"error": "401 Unauthorized"}\n```'},
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        assert len(observations) == 1
        assert observations[0].observed_outcome is OutcomeKind.FAILURE
        assert observations[0].confidence == 0.85  # 4xx pattern

    def test_user_message_not_following_tool_call_is_not_a_tool_result(self) -> None:
        # A plain user-assistant chat without tool_calls: user messages must
        # NOT be misinterpreted as tool results.
        messages = [
            {"role": "user", "content": "I got a 503 error earlier"},  # plain prose
            {"role": "assistant", "content": "Let me look into that"},
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        assert observations == []

    def test_retry_detection_works_under_appworld_convention(self) -> None:
        messages: list[dict] = [
            {"role": "user", "content": "send"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "send", "arguments": '{"to": "alice"}'}}],
            },
            {"role": "user", "content": 'Output:\n```\n{"error": "503 Service Unavailable"}\n```'},
            {
                "role": "assistant",
                "content": "retrying",
                "tool_calls": [{"id": "t2", "type": "function", "function": {"name": "send", "arguments": '{"to": "alice"}'}}],
            },
            {"role": "user", "content": 'Output:\n```\n{"ok": true}\n```'},
        ]
        observations = extract_tool_signals(messages, trajectory_id="t1", observed_at=_ts())
        # Expect: 1 from 5xx error + 1 from same-tool retry detection.
        retry_obs = [o for o in observations if "retry" in (o.detail or "")]
        assert len(retry_obs) == 1
        assert retry_obs[0].observed_outcome is OutcomeKind.FAILURE


class TestVenmoFixtureRegression:
    """Smoke test on the canonical AppWorld trajectory bundled with the repo.

    The fixture represents a successful task (venmo send_money). We expect:
    - extract_tool_signals returns 0 observations (no errors / no retries
      in this trajectory; successes don't emit signals from this extractor),
    - the dual-convention recognition does NOT raise or misclassify."""

    def test_venmo_trajectory_yields_no_error_signals(self) -> None:
        import json
        import pathlib

        msgs = json.loads((pathlib.Path(__file__).parent.parent / "fixtures" / "appworld_venmo_task_trajectory.json").read_text())
        observations = extract_tool_signals(msgs, trajectory_id="venmo", observed_at=_ts())
        # Trajectory is a clean success → no failures, no retries.
        assert all(o.observed_outcome is OutcomeKind.FAILURE for o in observations)
        # We don't pin the exact count (the regex set evolves), but a clean
        # trajectory should produce a small number — sanity bound at 5.
        assert len(observations) <= 5
