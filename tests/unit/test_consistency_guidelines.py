"""Tests for trajectory-to-IR transformation in consistency_guidelines.py."""

import pytest

from altk_evolve.llm.guidelines.consistency_guidelines import (
    _can_segment_trajectory,
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
        assert _is_well_formed_tool_calls([{"id": "1", "type": "function", "function": {"name": "add", "arguments": "{}"}}]) is True

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
            {"role": "assistant", "content": "none"},  # no tool_calls
            {"role": "tool", "content": "orphan"},  # orphaned → stripped
            {"role": "assistant", "tool_calls": [{"id": "2", "function": {"name": "f"}}]},
            {"role": "tool", "content": "valid"},  # valid → kept
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


class TestCanSegmentTrajectory:
    def test_non_empty_string_content_is_safe(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Thought: let me solve this."},
        ]
        assert _can_segment_trajectory(messages) is True

    def test_list_content_with_single_function_call_is_safe(self):
        messages = [
            {"role": "assistant", "content": [{"type": "function_call", "function": {"name": "add"}}]},
        ]
        assert _can_segment_trajectory(messages) is True

    def test_tool_calls_key_is_not_safe(self):
        # chat completions format: tool_calls key present, content null
        messages = [
            {"role": "assistant", "tool_calls": [{"function": {"name": "add"}}], "content": None},
        ]
        assert _can_segment_trajectory(messages) is False

    def test_list_content_with_two_function_calls_is_not_safe(self):
        # parallel tool calls: 2 parse_openai steps, 1 IR step → mismatch
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "function_call", "function": {"name": "add"}},
                    {"type": "function_call", "function": {"name": "multiply"}},
                ],
            },
        ]
        assert _can_segment_trajectory(messages) is False

    def test_list_content_with_zero_function_calls_is_not_safe(self):
        # 0 parse_openai steps, 1 IR step → mismatch
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]
        assert _can_segment_trajectory(messages) is False

    def test_empty_string_content_is_not_safe(self):
        # parse_openai skips empty strings; IR counts them
        messages = [{"role": "assistant", "content": ""}]
        assert _can_segment_trajectory(messages) is False

    def test_whitespace_only_content_is_not_safe(self):
        messages = [{"role": "assistant", "content": "   "}]
        assert _can_segment_trajectory(messages) is False

    def test_none_content_is_not_safe(self):
        messages = [{"role": "assistant", "content": None}]
        assert _can_segment_trajectory(messages) is False

    def test_non_assistant_messages_are_ignored(self):
        messages = [
            {"role": "system", "content": None},
            {"role": "user", "content": "What is 2+3?"},
            {"role": "tool", "content": "5"},
            {"role": "assistant", "content": "The answer is 5."},
        ]
        assert _can_segment_trajectory(messages) is True

    def test_mixed_safe_messages_are_safe(self):
        # string content step followed by single-function_call step
        messages = [
            {"role": "assistant", "content": "Thought: I will call add."},
            {"role": "assistant", "content": [{"type": "function_call", "function": {"name": "add"}}]},
        ]
        assert _can_segment_trajectory(messages) is True

    def test_one_unsafe_message_makes_whole_trajectory_unsafe(self):
        messages = [
            {"role": "assistant", "content": "Thought: I will call add."},
            {"role": "assistant", "tool_calls": [{"function": {"name": "add"}}], "content": None},
        ]
        assert _can_segment_trajectory(messages) is False

    def test_empty_messages_list_is_safe(self):
        # no assistant messages → no violations → safe (vacuously true)
        assert _can_segment_trajectory([]) is True


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
                "tool_calls": [{"function": {"name": "add", "arguments": '{"a": 2, "b": 3}'}}],
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

    def test_step_range_renders_only_steps_in_range(self):
        messages = [
            {"role": "assistant", "content": "step one"},
            {"role": "assistant", "content": "step two"},
            {"role": "assistant", "content": "step three"},
        ]
        result = format_trajectory_data(messages, {"step_uncertainties": {}}, step_range=(2, 3))
        assert "step one" not in result
        assert "step two" in result
        assert "step three" in result

    def test_step_range_single_step(self):
        messages = [
            {"role": "assistant", "content": "step one"},
            {"role": "assistant", "content": "step two"},
        ]
        result = format_trajectory_data(messages, {"step_uncertainties": {}}, step_range=(1, 1))
        assert "step one" in result
        assert "step two" not in result

    def test_step_range_filters_uncertainty_markers_to_range(self):
        messages = [
            {"role": "assistant", "content": "step one"},
            {"role": "assistant", "content": "step two"},
        ]
        # step 1 has high uncertainty, step 2 does not — range covers only step 2
        consistency_data = {"step_uncertainties": {1: 0.35, 2: 0.05}}
        result = format_trajectory_data(messages, consistency_data, step_range=(2, 2))
        assert "HIGH UNCERTAINTY" not in result
        assert "step two" in result

    def test_step_range_marks_uncertainty_within_range(self):
        messages = [
            {"role": "assistant", "content": "step one"},
            {"role": "assistant", "content": "step two"},
        ]
        consistency_data = {"step_uncertainties": {1: 0.05, 2: 0.35}}
        result = format_trajectory_data(messages, consistency_data, step_range=(2, 2))
        assert "HIGH UNCERTAINTY" in result
        assert "step two" in result


class TestSegmentationGuard:
    """Segmentation must not fire on single-step trajectories."""

    def _make_sampled_ir(self):
        return {
            "task": "test",
            "name": "Trajectory test",
            "steps": [
                {
                    "name": "AnyAgent_content",
                    "step_number": 1,
                    "raw_response": "answer",
                    "raw_response_type": "content",
                    "messages": [],
                    "llm_params": {"model": None},
                    "sampling": {"num_samples": 1, "raw_samples": ["answer"]},
                }
            ],
        }

    def test_single_step_trajectory_skips_segmentation(self):
        from unittest.mock import MagicMock, patch
        from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines

        mock_segment = MagicMock(
            return_value=[
                MagicMock(start_step=1, end_step=1, generalized_description="subtask A"),
                MagicMock(start_step=1, end_step=1, generalized_description="subtask B"),
                MagicMock(start_step=1, end_step=1, generalized_description="subtask C"),
            ]
        )
        mock_score_card = {"steps": [], "aggregate_trajectory_uncertainty": 0.5}
        mock_sampled_ir = self._make_sampled_ir()

        trajectory = {
            "trace_id": "test-single",
            "messages": [
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "The answer is 4."},
            ],
        }

        with (
            patch("altk_evolve.llm.guidelines.consistency_guidelines.resample_trajectory") as mock_resample,
            patch("altk_evolve.llm.guidelines.consistency_guidelines.analyze_consistency") as mock_analyze,
            patch("altk_evolve.llm.guidelines.consistency_guidelines._generate_guideline_result") as mock_gen,
            patch("altk_evolve.llm.guidelines.segmentation.segment_trajectory", mock_segment),
        ):
            mock_resample.return_value = mock_sampled_ir
            mock_analyze.return_value = (mock_score_card, mock_sampled_ir)
            mock_gen.return_value = MagicMock(guidelines=[])
            generate_consistency_guidelines(trajectory)
            # segment_trajectory should never have been called for a 1-step trajectory
            mock_segment.assert_not_called()
            # _generate_guideline_result called once (full trajectory), not 3× (per fake subtask)
            assert mock_gen.call_count == 1
            _, kwargs = mock_gen.call_args
            assert kwargs.get("step_range") is None
