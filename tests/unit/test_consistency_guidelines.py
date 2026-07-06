"""Tests for trajectory-to-IR transformation in consistency_guidelines.py."""

import pytest

from altk_evolve.llm.guidelines.consistency_guidelines import (
    _classify_step_response,
    _is_well_formed_tool_calls,
    _strip_orphaned_tool_messages,
    format_trajectory_data,
    parse_consistency_score_card,
    transform_trajectory_to_IR,
)

pytestmark = pytest.mark.unit


SAMPLE_TOOLS = [{"type": "function", "function": {"name": "add", "parameters": {}}}]


class TestIsWellFormedToolCalls:
    def test_well_formed_list(self):
        assert _is_well_formed_tool_calls(
            [{"id": "1", "type": "function", "function": {"name": "add", "arguments": "{}"}}]
        ) is True

    def test_empty_list_is_not_well_formed(self):
        assert _is_well_formed_tool_calls([]) is False

    def test_missing_function_name_is_not_well_formed(self):
        assert _is_well_formed_tool_calls([{"id": "1", "function": {}}]) is False

    def test_non_list_is_not_well_formed(self):
        assert _is_well_formed_tool_calls("not a list") is False
        assert _is_well_formed_tool_calls(None) is False


class TestClassifyStepResponse:
    def test_plain_content(self):
        response_type, raw = _classify_step_response({"role": "assistant", "content": "Hello there"})
        assert response_type == "content"
        assert raw == "Hello there"

    def test_well_formed_tool_calls(self):
        msg = {
            "role": "assistant",
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "add", "arguments": "{}"}}],
        }
        response_type, raw = _classify_step_response(msg)
        assert response_type == "tool_calls"
        assert "add" in raw

    def test_malformed_tool_calls_is_other(self):
        msg = {"role": "assistant", "tool_calls": [{"id": "1"}]}
        response_type, _ = _classify_step_response(msg)
        assert response_type == "other"

    def test_empty_content_and_no_tool_calls_is_other(self):
        response_type, _ = _classify_step_response({"role": "assistant", "content": None})
        assert response_type == "other"

    def test_blank_content_is_other(self):
        response_type, _ = _classify_step_response({"role": "assistant", "content": "   "})
        assert response_type == "other"


class TestTransformTrajectoryToIR:
    def test_openai_agent_naming_when_tools_present(self):
        """Trajectories with a real tools schema (native protocol, e.g. openai_agents) get
        the OpenAIAgent prefix."""
        trajectory = {
            "trace_id": "trace_abc12345",
            "model": "gpt-4o",
            "tools": SAMPLE_TOOLS,
            "messages": [
                {"role": "user", "content": "What is 2+3?"},
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "1", "type": "function", "function": {"name": "add", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": "1", "content": "5"},
                {"role": "assistant", "content": "The answer is 5."},
            ],
        }

        ir = transform_trajectory_to_IR(trajectory)

        step_names = [s["name"] for s in ir["steps"]]
        assert step_names == ["OpenAIAgent_tool_calls", "OpenAIAgent_content"]

    def test_any_agent_naming_when_tools_absent(self):
        """Trajectories without a real tools schema (e.g. smolagents' synthesized tool_calls
        steps) get the generic AnyAgent prefix instead, since they can't be resampled the
        same way as native tool-calling."""
        trajectory = {
            "trace_id": "trace_def67890",
            "model": "gpt-4o",
            "tools": None,
            "messages": [
                {"role": "user", "content": "What is 2+3?"},
                {"role": "assistant", "content": "result = add(2, 3)\nfinal_answer(result)"},
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "1", "type": "function", "function": {"name": "add", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": "1", "content": "5"},
            ],
        }

        ir = transform_trajectory_to_IR(trajectory)

        step_names = [s["name"] for s in ir["steps"]]
        assert step_names == ["AnyAgent_content", "AnyAgent_tool_calls"]

    def test_any_agent_other_for_degenerate_step(self):
        trajectory = {
            "trace_id": "trace_xyz11111",
            "model": "gpt-4o",
            "tools": None,
            "messages": [
                {"role": "user", "content": "Do something"},
                {"role": "assistant", "content": None},
            ],
        }

        ir = transform_trajectory_to_IR(trajectory)

        assert ir["steps"][0]["name"] == "AnyAgent_other"
        assert ir["steps"][0]["raw_response_type"] == "other"

    def test_tool_calls_step_carries_tools_schema(self):
        trajectory = {
            "trace_id": "trace_qqq99999",
            "model": "gpt-4o",
            "tools": SAMPLE_TOOLS,
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "1", "type": "function", "function": {"name": "add", "arguments": "{}"}}],
                },
            ],
        }

        ir = transform_trajectory_to_IR(trajectory)

        assert ir["steps"][0]["tools"] == SAMPLE_TOOLS


class TestStripOrphanedToolMessages:
    def test_keeps_tool_message_preceded_by_tool_calls(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "add"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "5"},
        ]
        result = _strip_orphaned_tool_messages(messages)
        assert len(result) == 3

    def test_strips_tool_message_with_no_preceding_tool_calls(self):
        """gen_ai format: intermediate assistant message has text only, no tool_calls —
        the following tool message must be removed or the LLM API returns a 400."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "thinking..."},
            {"role": "tool", "tool_call_id": "1", "content": "5"},
        ]
        result = _strip_orphaned_tool_messages(messages)
        assert result == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "thinking..."},
        ]

    def test_keeps_non_tool_messages_unchanged(self):
        messages = [
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert _strip_orphaned_tool_messages(messages) == messages

    def test_empty_list(self):
        assert _strip_orphaned_tool_messages([]) == []

    def test_strips_only_orphaned_not_all_tool_messages(self):
        """Second tool message is valid (preceded by tool_calls); first is orphaned."""
        messages = [
            {"role": "assistant", "content": "none"},          # no tool_calls
            {"role": "tool", "content": "orphan"},             # orphaned → stripped
            {"role": "assistant", "tool_calls": [{"id": "2", "function": {"name": "f"}}]},
            {"role": "tool", "content": "valid"},              # valid → kept
        ]
        result = _strip_orphaned_tool_messages(messages)
        tool_messages = [m for m in result if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0]["content"] == "valid"


class TestParseConsistencyScoreCard:
    def test_extracts_step_uncertainties(self):
        score_card = {
            "task": "Add two numbers",
            "aggregate_trajectory_uncertainty": 0.15,
            "steps": [
                {"step_number": 1, "step_uncertainty": 0.05},
                {"step_number": 2, "step_uncertainty": 0.25},
            ],
        }
        result = parse_consistency_score_card(score_card)
        assert result["step_uncertainties"] == {1: 0.05, 2: 0.25}
        assert result["task"] == "Add two numbers"
        assert result["aggregate_trajectory_uncertainty"] == 0.15

    def test_skips_steps_missing_uncertainty(self):
        score_card = {
            "steps": [
                {"step_number": 1, "step_uncertainty": 0.1},
                {"step_number": 2},  # no step_uncertainty
            ]
        }
        result = parse_consistency_score_card(score_card)
        assert 2 not in result["step_uncertainties"]
        assert 1 in result["step_uncertainties"]

    def test_empty_score_card(self):
        result = parse_consistency_score_card({})
        assert result["step_uncertainties"] == {}
        assert result["task"] is None


class TestFormatTrajectoryData:
    def test_includes_assistant_steps(self):
        messages = [
            {"role": "user", "content": "What is 2+3?"},
            {"role": "assistant", "content": "The answer is 5."},
        ]
        consistency_data = {"step_uncertainties": {1: 0.05}}
        result = format_trajectory_data(messages, consistency_data)
        assert "The answer is 5." in result
        assert "Step 1" in result

    def test_skips_non_assistant_messages(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "Be helpful."},
            {"role": "assistant", "content": "hello"},
        ]
        result = format_trajectory_data(messages, {"step_uncertainties": {}})
        assert "hi" not in result
        assert "Be helpful" not in result
        assert "hello" in result

    def test_marks_high_uncertainty_steps(self):
        messages = [
            {"role": "assistant", "content": "step one"},
            {"role": "assistant", "content": "step two"},
        ]
        consistency_data = {"step_uncertainties": {1: 0.05, 2: 0.30}}
        result = format_trajectory_data(messages, consistency_data)
        assert "HIGH UNCERTAINTY" in result
        assert "step two" in result

    def test_formats_tool_calls_step(self):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "add", "arguments": '{"a": 2, "b": 3}'}}
                ],
            }
        ]
        result = format_trajectory_data(messages, {"step_uncertainties": {}})
        assert "add(" in result
        assert "Agent tool calls" in result

    def test_truncates_long_content(self):
        long_content = "x" * 600
        messages = [{"role": "assistant", "content": long_content}]
        result = format_trajectory_data(messages, {"step_uncertainties": {}})
        assert "..." in result
        assert len(result) < len(long_content)
