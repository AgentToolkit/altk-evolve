"""Tests for trajectory-to-IR transformation in consistency_guidelines.py."""

import json

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

from altk_evolve.schema.guidelines import GuidelineGenerationResult

pytestmark = pytest.mark.unit

# Sentinel for "omit the key entirely", distinct from any value the key could hold.
_UNSET = object()


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
        """Trajectories without a real tools schema get the AnyAgent prefix for content steps.
        AnyAgent_tool_calls steps are skipped because we lack the tools schema needed to
        instruct the model to call tools — resampling without it would produce bogus results."""
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
        # AnyAgent_tool_calls is skipped — no tools schema means we can't faithfully resample
        assert step_names == ["AnyAgent_content"]
        # Positional numbering: content step is position 1, skipped tool_calls step
        # consumes position 2 — so the scorable step retains step_number=1.
        assert ir["steps"][0]["step_number"] == 1

    def test_skipped_step_advances_step_number(self):
        """A skipped step (AnyAgent_tool_calls) consumes its positional slot so that
        the scorable step that follows gets the correct positional step_number.
        This keeps IR step_numbers aligned with format_trajectory_data's positional
        counting, preventing uncertainty markers from landing on the wrong step."""
        trajectory = {
            "trace_id": "trace_skip_test",
            "model": "gpt-4o",
            "tools": None,
            "messages": [
                {"role": "user", "content": "Pay rent"},
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "1", "type": "function", "function": {"name": "transfer", "arguments": "{}"}}],
                },  # skipped (AnyAgent_tool_calls) — consumes position 1
                {"role": "assistant", "content": "Rent has been paid successfully."},  # position 2
            ],
        }

        ir = transform_trajectory_to_IR(trajectory)

        assert [s["name"] for s in ir["steps"]] == ["AnyAgent_content"]
        # The reasoning step follows the skipped tool_calls step, so it must be
        # step_number=2, not 1 — matching format_trajectory_data's positional count.
        assert ir["steps"][0]["step_number"] == 2

    def test_other_and_unscorable_steps_are_skipped(self):
        """Steps classified as 'other' (malformed/degenerate) are excluded from the IR —
        they cannot be meaningfully resampled, so creating a step for them would only
        incur LLM cost and then be silently dropped by the scorer."""
        trajectory = {
            "trace_id": "trace_xyz11111",
            "model": "gpt-4o",
            "tools": None,
            "messages": [
                {"role": "user", "content": "Do something"},
                {"role": "assistant", "content": None},  # degenerate → "other"
            ],
        }

        ir = transform_trajectory_to_IR(trajectory)

        assert ir["steps"] == []

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
    """Tests for format_trajectory_data's step rendering and uncertainty markers."""

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
        """Uncertainty markers still apply correctly to steps kept by a step_range filter."""
        messages = [
            {"role": "assistant", "content": "step one"},
            {"role": "assistant", "content": "step two"},
        ]
        consistency_data = {"step_uncertainties": {1: 0.05, 2: 0.35}}
        result = format_trajectory_data(messages, consistency_data, step_range=(2, 2))
        assert "HIGH UNCERTAINTY" in result
        assert "step two" in result

    def test_marks_elevated_not_high_when_below_high_threshold(self):
        """A non-zero score below the high threshold must be labeled ELEVATED, not
        HIGH — the label must never claim a threshold that wasn't actually met
        (default high=0.15)."""
        messages = [
            {"role": "assistant", "content": "step one"},
            {"role": "assistant", "content": "step two"},
        ]
        consistency_data = {"step_uncertainties": {1: 0.02, 2: 0.1029}}
        result = format_trajectory_data(messages, consistency_data)
        assert "ELEVATED UNCERTAINTY: 0.1029" in result
        assert "HIGH UNCERTAINTY" not in result

    def test_elevated_marks_only_the_top_step(self):
        """The ELEVATED fallback flags just the most-uncertain step, so a trajectory of
        uniformly-small scores doesn't end up marked end to end."""
        messages = [
            {"role": "assistant", "content": "step one"},
            {"role": "assistant", "content": "step two"},
        ]
        consistency_data = {"step_uncertainties": {1: 0.02, 2: 0.05}}
        result = format_trajectory_data(messages, consistency_data)
        assert result.count("ELEVATED UNCERTAINTY") == 1
        assert "ELEVATED UNCERTAINTY: 0.05" in result
        assert "ELEVATED UNCERTAINTY: 0.02" not in result
        assert "HIGH UNCERTAINTY" not in result

    def test_elevated_stays_single_across_many_sub_threshold_steps(self):
        """Adding more sub-threshold steps never widens the ELEVATED marker past the top one."""
        messages = [{"role": "assistant", "content": f"step {i}"} for i in range(1, 6)]
        consistency_data = {"step_uncertainties": {1: 0.01, 2: 0.02, 3: 0.03, 4: 0.04, 5: 0.05}}
        result = format_trajectory_data(messages, consistency_data)
        assert result.count("ELEVATED UNCERTAINTY") == 1
        assert "ELEVATED UNCERTAINTY: 0.05" in result
        assert "HIGH UNCERTAINTY" not in result

    def test_no_marker_when_every_step_is_fully_consistent(self):
        """Zero is the only score that earns no marker at all — with one threshold left,
        every trajectory carrying measurable uncertainty gets at least one flag."""
        messages = [
            {"role": "assistant", "content": "step one"},
            {"role": "assistant", "content": "step two"},
        ]
        consistency_data = {"step_uncertainties": {1: 0.0, 2: 0.0}}
        result = format_trajectory_data(messages, consistency_data)
        assert "HIGH UNCERTAINTY" not in result
        assert "ELEVATED UNCERTAINTY" not in result

    def test_caps_high_markers_at_five(self):
        """At most HIGH_MARKER_CAP steps carry the HIGH marker, even when more clear the
        threshold — the lowest-scoring ones above the bar go unmarked."""
        messages = [{"role": "assistant", "content": f"step {i}"} for i in range(1, 8)]
        consistency_data = {"step_uncertainties": {1: 0.9, 2: 0.8, 3: 0.7, 4: 0.6, 5: 0.5, 6: 0.4, 7: 0.3}}
        result = format_trajectory_data(messages, consistency_data)
        assert result.count("HIGH UNCERTAINTY") == 5
        assert "HIGH UNCERTAINTY: 0.4" not in result
        assert "HIGH UNCERTAINTY: 0.3" not in result
        # The ELEVATED fallback stays silent whenever any step cleared the threshold.
        assert "ELEVATED UNCERTAINTY" not in result

    def test_out_of_window_step_cannot_steal_a_marker_slot(self):
        """The ranking is clamped to the rendered window, like the loop. Otherwise an
        out-of-window step wins the HIGH slot — or the single ELEVATED slot — and then never
        renders, so a trajectory kept alive by an in-window step shows no marker at all."""
        from altk_evolve.llm.guidelines.consistency_guidelines import MAX_RENDERED_STEPS

        messages = [{"role": "assistant", "content": f"step {i}"} for i in range(MAX_RENDERED_STEPS + 10)]
        out_of_window = MAX_RENDERED_STEPS + 5

        for label, scores in [
            ("out-of-window would take HIGH", {5: 0.02, out_of_window: 0.9}),
            ("out-of-window would take ELEVATED", {5: 0.02, out_of_window: 0.09}),
        ]:
            result = format_trajectory_data(messages, {"step_uncertainties": scores})
            markers = result.count("HIGH UNCERTAINTY") + result.count("ELEVATED UNCERTAINTY")
            assert markers == 1, label

    def test_marker_does_not_depend_on_uncertainty_insertion_order(self):
        """`sorted` is stable, so with equal scores an unclamped ranking let dict insertion
        order decide whether a marker appeared at all."""
        from altk_evolve.llm.guidelines.consistency_guidelines import MAX_RENDERED_STEPS

        messages = [{"role": "assistant", "content": f"step {i}"} for i in range(MAX_RENDERED_STEPS + 10)]
        far = MAX_RENDERED_STEPS + 5

        in_first = format_trajectory_data(messages, {"step_uncertainties": {5: 0.02, far: 0.02}})
        out_first = format_trajectory_data(messages, {"step_uncertainties": {far: 0.02, 5: 0.02}})

        assert in_first.count("ELEVATED UNCERTAINTY") == 1
        assert out_first.count("ELEVATED UNCERTAINTY") == 1

    def test_tool_calls_none_does_not_crash(self):
        # Raw OpenAI message dumps always carry tool_calls: null
        messages = [{"role": "assistant", "content": "hello", "tool_calls": None}]
        result = format_trajectory_data(messages, {"step_uncertainties": {}})
        assert "Agent reasoning" in result

    def test_tool_calls_invalid_json_args_preserves_name_and_raw_args(self):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "execute_sql", "arguments": "SELECT * FROM t WHERE"}}],
            }
        ]
        result = format_trajectory_data(messages, {"step_uncertainties": {}})
        # Function name and raw args must both be preserved — not just the call id
        assert "execute_sql" in result
        assert "SELECT * FROM t WHERE" in result

    def test_tool_calls_non_object_json_args_does_not_crash(self):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "run", "arguments": '"just_a_string"'}}],
            }
        ]
        result = format_trajectory_data(messages, {"step_uncertainties": {}})
        assert "run" in result

    def test_tool_calls_missing_function_key_does_not_crash(self):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [{"id": "call_1"}],
            }
        ]
        result = format_trajectory_data(messages, {"step_uncertainties": {}})
        assert "Agent tool calls" in result


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
        """A trajectory with too few scorable steps falls back to full-trajectory generation
        instead of segmenting, even if the segmenter itself returns subtasks."""
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


@pytest.mark.unit
class TestSegmentationFloorAndFastPathScope:
    """The two segmentation changes, each of which survived a full-suite mutation before."""

    def _fast_trajectory(self, n_steps, with_tool_calls=False):
        """A trajectory whose parse_openai_agents_trajectory steps_list has n_steps entries."""
        messages = [{"role": "user", "content": "do the thing"}]
        for i in range(n_steps):
            if with_tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": f"c{i}", "type": "function", "function": {"name": "act", "arguments": "{}"}},
                            {"id": f"d{i}", "type": "function", "function": {"name": "also", "arguments": "{}"}},
                        ],
                    }
                )
                messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
            else:
                messages.append({"role": "assistant", "content": f"reasoning step {i}"})
        return {"trace_id": "t", "messages": messages}

    def _run_fast(self, trajectory, subtasks, monkeypatch):
        """Run the fast pipeline with segment_trajectory stubbed; return its mock.

        segmentation_enabled is forced on: it is a deployment setting that a local .env can
        switch off, and these tests are about the gate's own conditions, not that setting.
        """
        from unittest.mock import MagicMock, patch

        from altk_evolve.llm.guidelines import consistency_guidelines as module
        from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines_fast

        monkeypatch.setattr(module.evolve_config, "segmentation_enabled", True)

        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps({"guidelines": []})

        with (
            patch("altk_evolve.llm.guidelines.segmentation.segment_trajectory", return_value=subtasks) as mock_segment,
            patch("altk_evolve.llm.guidelines.consistency_guidelines.completion", return_value=response),
            patch("altk_evolve.llm.guidelines.consistency_guidelines.supports_response_schema", return_value=True),
            patch("altk_evolve.llm.guidelines.consistency_guidelines.get_supported_openai_params", return_value=["response_format"]),
        ):
            generate_consistency_guidelines_fast(trajectory)
        return mock_segment

    def _subtask(self, start, end):
        from altk_evolve.schema.guidelines import SubtaskSegment

        return SubtaskSegment(generalized_description="d", start_step=start, end_step=end, purpose="p")

    def test_fast_path_segments_a_native_tool_calls_trajectory(self, monkeypatch):
        """Regression for a capability, not a bug: `_can_segment_trajectory` guards alignment
        with transform_trajectory_to_IR, which the fast path never builds. Gating on it here
        disabled segmentation for native tool_calls and for parallel calls in one message —
        both of which parse_openai_agents_trajectory handles."""
        trajectory = self._fast_trajectory(3, with_tool_calls=True)  # 3 msgs x 2 calls = 6 steps
        mock_segment = self._run_fast(trajectory, [self._subtask(1, 3), self._subtask(4, 6)], monkeypatch)
        mock_segment.assert_called_once()

    def test_fast_path_floor_does_not_segment_below_the_minimum(self, monkeypatch):
        from altk_evolve.llm.guidelines.consistency_guidelines import SEGMENTATION_MIN_STEPS

        mock_segment = self._run_fast(self._fast_trajectory(SEGMENTATION_MIN_STEPS - 1), [], monkeypatch)
        mock_segment.assert_not_called()

    def test_fast_path_floor_segments_at_the_minimum(self, monkeypatch):
        from altk_evolve.llm.guidelines.consistency_guidelines import SEGMENTATION_MIN_STEPS

        n = SEGMENTATION_MIN_STEPS
        mock_segment = self._run_fast(self._fast_trajectory(n), [self._subtask(1, 2), self._subtask(3, n)], monkeypatch)
        mock_segment.assert_called_once()

    @pytest.mark.parametrize("n_scorable,should_segment", [(4, False), (5, True)])
    def test_accurate_path_floor_boundary(self, n_scorable, should_segment):
        """Pins the 2 -> 5 floor: reverting it left the whole suite green."""
        from unittest.mock import patch

        from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines

        messages = [{"role": "user", "content": "go"}]
        messages += [{"role": "assistant", "content": f"step {i}"} for i in range(n_scorable)]
        ir = {
            "task": "go",
            "name": "T",
            "steps": [{"name": "AnyAgent_content", "step_number": i + 1, "sampling": {"num_samples": 2}} for i in range(n_scorable)],
        }
        score_card = {"steps": [{"step_number": i + 1, "step_uncertainty": 0.5} for i in range(n_scorable)]}

        with (
            patch("altk_evolve.llm.guidelines.consistency_guidelines.transform_trajectory_to_IR", return_value=ir),
            patch("altk_evolve.llm.guidelines.consistency_guidelines.resample_trajectory", return_value=ir),
            patch("altk_evolve.llm.guidelines.consistency_guidelines.analyze_consistency", return_value=(score_card, ir)),
            patch("altk_evolve.llm.guidelines.segmentation.segment_trajectory", return_value=[]) as mock_segment,
            patch("altk_evolve.llm.guidelines.consistency_guidelines._generate_guideline_result") as mock_gen,
        ):
            mock_gen.return_value = GuidelineGenerationResult(guidelines=[], task_description="go")
            generate_consistency_guidelines({"trace_id": "t", "messages": messages})

        assert mock_segment.called is should_segment


@pytest.mark.unit
class TestSkipGateWindow:
    """The skip gate must agree with what the prompt can actually render."""

    def _run_gate(self, step_uncertainties, n_messages):
        from unittest.mock import MagicMock, patch

        from altk_evolve.llm.guidelines.consistency_guidelines import _generate_guideline_result

        messages = [{"role": "assistant", "content": f"s{i}"} for i in range(n_messages)]
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps({"guidelines": []})
        with patch("altk_evolve.llm.guidelines.consistency_guidelines.completion", return_value=response) as mock_completion:
            _generate_guideline_result(
                messages=messages,
                consistency_data={"step_uncertainties": step_uncertainties},
                task_description="t",
                step_range=None,
                constrained_decoding_supported=False,
                debug_suffix="",
            )
        return mock_completion.called

    def test_skips_when_the_only_uncertain_step_is_past_the_rendered_window(self):
        """MAX_RENDERED_STEPS stops the renderer, so uncertainty beyond it can never be
        marked. Generating anyway sends a prompt that explains markers it does not contain."""
        from altk_evolve.llm.guidelines.consistency_guidelines import MAX_RENDERED_STEPS

        assert self._run_gate({MAX_RENDERED_STEPS + 5: 0.5}, n_messages=MAX_RENDERED_STEPS + 10) is False

    def test_generates_when_the_uncertain_step_is_inside_the_window(self):
        from altk_evolve.llm.guidelines.consistency_guidelines import MAX_RENDERED_STEPS

        assert self._run_gate({5: 0.5}, n_messages=MAX_RENDERED_STEPS + 10) is True

    def test_an_in_window_step_keeps_a_mixed_trajectory_alive(self):
        """Mixed in-window and out-of-window uncertainty: the gate must key on the in-window
        step alone, and format_trajectory_data must then actually mark it."""
        from altk_evolve.llm.guidelines.consistency_guidelines import MAX_RENDERED_STEPS, format_trajectory_data

        scores = {5: 0.02, MAX_RENDERED_STEPS + 5: 0.9}
        assert self._run_gate(scores, n_messages=MAX_RENDERED_STEPS + 10) is True

        messages = [{"role": "assistant", "content": f"s{i}"} for i in range(MAX_RENDERED_STEPS + 10)]
        rendered = format_trajectory_data(messages, {"step_uncertainties": scores})
        assert rendered.count("HIGH UNCERTAINTY") + rendered.count("ELEVATED UNCERTAINTY") == 1

    def test_skips_only_on_all_zero_uncertainty(self):
        """Pins the gate's `> 0`: raising it to a threshold left the whole suite green."""
        assert self._run_gate({1: 0.0, 2: 0.0}, n_messages=3) is False
        assert self._run_gate({1: 0.0, 2: 0.05}, n_messages=3) is True


@pytest.mark.unit
class TestHighUncertaintyThresholdValidation:
    """A threshold outside [0, 1] must fail loudly rather than silently marking nothing HIGH."""

    def _config(self, tmp_path, threshold):
        """Write a minimal analyzer config carrying `threshold`, and return its path."""
        body = "name: t\naggregation: mean\nmax_samples: 5\nmax_steps: 15\nagents: []\n"
        if threshold is not _UNSET:
            body += f"high_uncertainty_threshold: {threshold}\n"
        path = tmp_path / "agent_config.yaml"
        path.write_text(body)
        return path

    def _run(self, tmp_path, threshold):
        from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines

        return generate_consistency_guidelines(
            {"messages": [{"role": "user", "content": "hi"}], "trace_id": "t"},
            config_path=self._config(tmp_path, threshold),
        )

    @pytest.mark.parametrize("threshold", ["15", "-0.1", "1.5", "'0.15'", "true", "0", "0.0"])
    def test_rejects_out_of_range_or_non_numeric(self, tmp_path, threshold):
        """0 is rejected at the low end: it marks every fully-consistent step HIGH, which the
        gate's own > 0 semantics treat as not uncertain at all."""
        from altk_evolve.schema.exceptions import EvolveException

        with pytest.raises(EvolveException, match="high_uncertainty_threshold must be a number"):
            self._run(tmp_path, threshold)

    @pytest.mark.parametrize("threshold", ["0.0001", "0.15", "1", _UNSET])
    def test_accepts_in_range_values_and_an_absent_key(self, tmp_path, threshold):
        """Valid thresholds get past validation — the later failure proves it wasn't the threshold."""
        from altk_evolve.schema.exceptions import EvolveException

        # The trajectory has no assistant turns, so generation fails *after* validation.
        with pytest.raises(EvolveException, match="no steps"):
            self._run(tmp_path, threshold)


@pytest.mark.unit
class TestConsistencyResponseRepair:
    """Both consistency pipelines must route responses through the repairing parser.

    The repairs themselves are covered in test_guidelines.py; these only prove the wiring,
    so a future refactor can't silently drop the rescue from one pipeline.
    """

    _GUIDELINE = {
        "content": "Re-read the tool output before answering",
        "rationale": "Prevents answering from a stale assumption",
        "category": "strategy",
        "trigger": "After any tool call",
    }

    def _bare_array_response(self):
        from unittest.mock import MagicMock

        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps([self._GUIDELINE])
        return response

    def test_accurate_pipeline_recovers_a_bare_array_response(self, caplog):
        import logging

        from unittest.mock import patch

        from altk_evolve.llm.guidelines.consistency_guidelines import _generate_guideline_result

        with patch("altk_evolve.llm.guidelines.consistency_guidelines.completion") as mock_completion, caplog.at_level(logging.INFO):
            mock_completion.return_value = self._bare_array_response()
            result = _generate_guideline_result(
                messages=[{"role": "assistant", "content": "step one"}],
                consistency_data={"step_uncertainties": {1: 0.5}},
                task_description="Answer a question",
                step_range=None,
                constrained_decoding_supported=False,
                debug_suffix="",
            )

        assert [g.content for g in result.guidelines] == ["Re-read the tool output before answering"]
        # The label is the only thing distinguishing the two consistency pipelines in logs,
        # which is what the "keep off-contract models visible" rationale depends on.
        assert "Recovered consistency guideline response" in caplog.text
        assert "fast consistency" not in caplog.text

    def test_fast_pipeline_recovers_a_bare_array_response(self, caplog):
        import logging

        from unittest.mock import patch

        from altk_evolve.llm.guidelines.consistency_guidelines import _generate_fast_guideline_result

        with patch("altk_evolve.llm.guidelines.consistency_guidelines.completion") as mock_completion, caplog.at_level(logging.INFO):
            mock_completion.return_value = self._bare_array_response()
            result = _generate_fast_guideline_result(
                task_description="Answer a question",
                trajectory_slice="Step 1 - Agent reasoning:\nstep one",
                num_steps=1,
                constrained_decoding_supported=False,
            )

        assert [g.content for g in result.guidelines] == ["Re-read the tool output before answering"]
        assert "Recovered fast consistency guideline response" in caplog.text


class TestGenerateConsistencyGuidelinesFast:
    """The fast consistency pipeline must never resample or score externally."""

    def _mock_completion_response(self, payload: dict):
        """Build a MagicMock litellm completion response whose message content is `payload` as JSON."""
        from unittest.mock import MagicMock

        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = __import__("json").dumps(payload)
        return response

    def test_fast_pipeline_never_resamples_or_analyzes_consistency(self, monkeypatch):
        """The fast pipeline calls the LLM once and never touches resample_trajectory/analyze_consistency."""
        from unittest.mock import patch

        from altk_evolve.llm.guidelines import consistency_guidelines as consistency_guidelines_module
        from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines_fast

        monkeypatch.setattr(consistency_guidelines_module.evolve_config, "segmentation_enabled", False)

        trajectory = {
            "trace_id": "test-fast-1",
            "messages": [
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "The answer is 4."},
            ],
        }

        with (
            patch("altk_evolve.llm.guidelines.consistency_guidelines.resample_trajectory") as mock_resample,
            patch("altk_evolve.llm.guidelines.consistency_guidelines.analyze_consistency") as mock_analyze,
            patch("altk_evolve.llm.guidelines.consistency_guidelines.completion") as mock_completion,
            patch("altk_evolve.llm.guidelines.consistency_guidelines.supports_response_schema", return_value=True),
            patch("altk_evolve.llm.guidelines.consistency_guidelines.get_supported_openai_params", return_value=["response_format"]),
        ):
            mock_completion.return_value = self._mock_completion_response(
                {
                    "guidelines": [
                        {
                            "content": "Double-check arithmetic before answering.",
                            "rationale": "Prevents silent calculation errors",
                            "category": "strategy",
                            "trigger": "When answering a math question",
                            "implementation_steps": ["Recompute the result", "Compare against the stated answer"],
                        }
                    ]
                }
            )

            results = generate_consistency_guidelines_fast(trajectory)

            mock_resample.assert_not_called()
            mock_analyze.assert_not_called()
            mock_completion.assert_called_once()
            assert results[0].guidelines[0].content == "Double-check arithmetic before answering."

    def test_fast_pipeline_prompt_asks_llm_to_self_judge_confidence(self, monkeypatch):
        """The rendered prompt asks the LLM to judge step confidence itself, with no
        resampling-derived uncertainty markers (those belong to the accurate pipeline only)."""
        from unittest.mock import patch

        from altk_evolve.llm.guidelines import consistency_guidelines as consistency_guidelines_module
        from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines_fast

        monkeypatch.setattr(consistency_guidelines_module.evolve_config, "segmentation_enabled", False)

        trajectory = {
            "messages": [
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "The answer is 4."},
            ],
        }

        with (
            patch("altk_evolve.llm.guidelines.consistency_guidelines.completion") as mock_completion,
            patch("altk_evolve.llm.guidelines.consistency_guidelines.supports_response_schema", return_value=True),
            patch("altk_evolve.llm.guidelines.consistency_guidelines.get_supported_openai_params", return_value=["response_format"]),
        ):
            mock_completion.return_value = self._mock_completion_response({"guidelines": []})

            generate_consistency_guidelines_fast(trajectory)

            _, kwargs = mock_completion.call_args
            prompt = kwargs["messages"][-1]["content"]
            assert "judge" in prompt.lower()
            assert "⚠️" not in prompt
            assert "HIGH UNCERTAINTY" not in prompt
