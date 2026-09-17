"""Tests for guideline generation utilities."""

import json
from unittest.mock import MagicMock, patch

import pytest

from altk_evolve.llm.guidelines import guidelines as guidelines_module
from altk_evolve.llm.guidelines.guidelines import generate_guidelines, parse_openai_agents_trajectory
from altk_evolve.schema.guidelines import SubtaskSegment


def _mock_completion_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    return response


@pytest.mark.unit
class TestParseOpenaiAgentsTrajectory:
    def test_extracts_task_instruction_from_first_user_message(self):
        messages = [
            {"role": "user", "content": "Fix the login bug"},
            {"role": "assistant", "content": "I'll look into that."},
        ]
        result = parse_openai_agents_trajectory(messages)
        assert result["task_instruction"] == "Fix the login bug"

    def test_fallback_when_no_user_message(self):
        messages = [{"role": "assistant", "content": "some response"}]
        result = parse_openai_agents_trajectory(messages)
        assert result["task_instruction"] == "Task description unknown"

    def test_fallback_when_empty_messages(self):
        result = parse_openai_agents_trajectory([])
        assert result["task_instruction"] == "Task description unknown"

    def test_extracts_native_chat_completions_tool_calls(self):
        """Native Chat Completions / Phoenix shape: content is null, call list lives in
        tool_calls. Regression for a step being silently dropped (empty content fell
        through to the "skip empty assistant messages" branch)."""
        messages = [
            {"role": "user", "content": "What is the weather in Paris?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "18C sunny"},
            {"role": "assistant", "content": "It is 18C and sunny in Paris."},
        ]
        result = parse_openai_agents_trajectory(messages)

        assert result["num_steps"] == 2
        assert len(result["function_calls"]) == 1
        assert result["function_calls"][0]["name"] == "get_weather"
        assert result["function_calls"][0]["call_id"] == "call_1"
        assert 'get_weather(city="Paris")' in result["trajectory_summary"]

    def test_native_tool_call_with_json_array_arguments_falls_back_to_raw(self):
        """arguments decoding to a JSON array (not an object) must not crash — .items()
        only applies to dict arguments, everything else uses the raw-string fallback."""
        messages = [
            {"role": "user", "content": "Log these values"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "log_values", "arguments": "[1, 2, 3]"}}],
            },
        ]
        result = parse_openai_agents_trajectory(messages)

        assert len(result["function_calls"]) == 1
        assert "log_values([1, 2, 3])" in result["trajectory_summary"]

    def test_native_tool_call_alongside_text_content_is_not_dropped(self):
        """An Anthropic-shape turn `[{"type": "text", ...}, {"type": "tool_use", ...}]`
        collapsed into one Chat Completions message carries both a non-empty `content`
        string and `tool_calls`. Regression: the text/tool_calls branches were `elif`,
        so the tool call was silently dropped whenever text content was also present."""
        messages = [
            {"role": "user", "content": "What is the weather in Paris?"},
            {
                "role": "assistant",
                "content": "Let me check the weather for you.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                    }
                ],
            },
        ]
        result = parse_openai_agents_trajectory(messages)

        assert len(result["function_calls"]) == 1
        assert result["function_calls"][0]["name"] == "get_weather"
        assert result["num_steps"] == 2
        assert "Let me check the weather for you." in result["trajectory_summary"]
        assert 'get_weather(city="Paris")' in result["trajectory_summary"]

    def test_native_tool_call_with_non_string_arguments_falls_back_to_raw(self):
        """arguments that aren't a string at all (already-parsed, non-mapping) must not
        crash — falls back to the raw-string fallback rather than calling .items()."""
        messages = [
            {"role": "user", "content": "Set the count"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "set_count", "arguments": 5}}],
            },
        ]
        result = parse_openai_agents_trajectory(messages)

        assert len(result["function_calls"]) == 1
        assert "set_count(5)" in result["trajectory_summary"]

    @patch("altk_evolve.llm.guidelines.guidelines.completion")
    @patch("altk_evolve.llm.guidelines.guidelines.supports_response_schema", return_value=True)
    @patch("altk_evolve.llm.guidelines.guidelines.get_supported_openai_params", return_value=["response_format"])
    def test_generate_guidelines_uses_json_prompt_for_groq_even_when_schema_is_reported(
        self,
        _mock_params,
        _mock_schema,
        mock_completion,
        monkeypatch,
    ):
        monkeypatch.setattr(guidelines_module.llm_settings, "guidelines_model", "groq/openai/gpt-oss-120b")
        monkeypatch.setattr(guidelines_module.llm_settings, "custom_llm_provider", "groq")
        monkeypatch.setattr(guidelines_module.evolve_config, "segmentation_enabled", False)
        mock_completion.return_value = _mock_completion_response(
            {
                "guidelines": [
                    {
                        "content": "Validate files before parsing",
                        "rationale": "Avoids parser crashes on empty inputs",
                        "category": "strategy",
                        "trigger": "Before reading user-provided CSV files",
                        "implementation_steps": ["Check file size", "Return an empty DataFrame for empty files"],
                    }
                ]
            }
        )

        results = generate_guidelines([{"role": "user", "content": "Fix CSV parsing"}])

        assert results[0].guidelines[0].content == "Validate files before parsing"
        _, kwargs = mock_completion.call_args
        assert "response_format" not in kwargs
        assert kwargs["custom_llm_provider"] == "groq"
        assert "Output Format (JSON)" in kwargs["messages"][0]["content"]


@pytest.mark.unit
class TestSegmentationFlag:
    """`EVOLVE_SEGMENTATION_ENABLED` must gate segmentation in both directions.

    The default value itself is pinned in tests/unit/test_evolve_config.py; these tests pin
    the *behaviour* on each side of the gate and set the flag explicitly, so they stay
    deterministic regardless of what the developer has in their environment.
    """

    # 1 user + 2 assistant turns, so num_steps == 2 and two single-step subtasks both slice
    # validly. Fewer steps than that and the segmented path could not be reached at all.
    MESSAGES = [
        {"role": "user", "content": "Find config.yaml in /etc/acme and summarize it"},
        {"role": "assistant", "content": "Searching /etc/acme for config.yaml."},
        {"role": "assistant", "content": "It sets retries=3 and a 30s timeout."},
    ]
    PAYLOAD = {
        "guidelines": [
            {
                "content": "Confirm a config file exists before parsing it",
                "rationale": "Avoids a crash on a missing path",
                "category": "strategy",
                "trigger": "Before opening a config file by name",
                "implementation_steps": ["Stat the path", "Report a clear error when it is absent"],
            }
        ]
    }

    @patch("altk_evolve.llm.guidelines.guidelines.completion")
    @patch("altk_evolve.llm.guidelines.guidelines.supports_response_schema", return_value=True)
    @patch("altk_evolve.llm.guidelines.guidelines.get_supported_openai_params", return_value=["response_format"])
    def test_disabled_skips_segmentation_and_keeps_user_message_verbatim(
        self,
        _mock_params,
        _mock_schema,
        mock_completion,
        monkeypatch,
    ):
        """Flag off: one LLM call for the whole trajectory, the segmenter is never reached,
        and task_description is the first user message verbatim. That last assertion is the
        documented cost of the default — task_description is both the clustering key
        (clustering.py) and the retrieval ranking key (retrieval.py), so it carries whatever
        the user typed, user-specific values included."""
        monkeypatch.setattr(guidelines_module.evolve_config, "segmentation_enabled", False)
        mock_completion.return_value = _mock_completion_response(self.PAYLOAD)

        with patch("altk_evolve.llm.guidelines.segmentation.segment_trajectory") as mock_segment:
            results = generate_guidelines(self.MESSAGES)

        mock_segment.assert_not_called()
        mock_completion.assert_called_once()
        assert len(results) == 1
        assert results[0].task_description == "Find config.yaml in /etc/acme and summarize it"

    @patch("altk_evolve.llm.guidelines.guidelines.completion")
    @patch("altk_evolve.llm.guidelines.guidelines.supports_response_schema", return_value=True)
    @patch("altk_evolve.llm.guidelines.guidelines.get_supported_openai_params", return_value=["response_format"])
    def test_enabled_segments_and_carries_generalized_descriptions(
        self,
        _mock_params,
        _mock_schema,
        mock_completion,
        monkeypatch,
    ):
        """Flag on: the segmenter runs once over the raw messages and each subtask becomes its
        own result carrying that subtask's generalized description. Proves the new default is
        a gate rather than a kill switch — opting back in still works."""
        monkeypatch.setattr(guidelines_module.evolve_config, "segmentation_enabled", True)
        mock_completion.return_value = _mock_completion_response(self.PAYLOAD)

        subtasks = [
            SubtaskSegment(
                generalized_description="Locate a named config file under a directory",
                purpose="Find the file to read",
                start_step=1,
                end_step=1,
            ),
            SubtaskSegment(
                generalized_description="Summarize the settings a config file declares",
                purpose="Report the configured values",
                start_step=2,
                end_step=2,
            ),
        ]

        with patch("altk_evolve.llm.guidelines.segmentation.segment_trajectory", return_value=subtasks) as mock_segment:
            results = generate_guidelines(self.MESSAGES)

        mock_segment.assert_called_once_with(self.MESSAGES)
        assert mock_completion.call_count == 2
        assert [r.task_description for r in results] == [s.generalized_description for s in subtasks]

    @patch("altk_evolve.llm.guidelines.guidelines.completion")
    @patch("altk_evolve.llm.guidelines.guidelines.supports_response_schema", return_value=True)
    @patch("altk_evolve.llm.guidelines.guidelines.get_supported_openai_params", return_value=["response_format"])
    def test_enabled_but_segmenter_raises_falls_back_to_full_trajectory(
        self,
        _mock_params,
        _mock_schema,
        mock_completion,
        monkeypatch,
    ):
        """Opting in must not make generation fail closed: a segmenter error degrades to the
        full-trajectory path instead of propagating."""
        monkeypatch.setattr(guidelines_module.evolve_config, "segmentation_enabled", True)
        mock_completion.return_value = _mock_completion_response(self.PAYLOAD)

        with patch(
            "altk_evolve.llm.guidelines.segmentation.segment_trajectory",
            side_effect=RuntimeError("segmenter unavailable"),
        ):
            results = generate_guidelines(self.MESSAGES)

        assert len(results) == 1
        assert results[0].task_description == "Find config.yaml in /etc/acme and summarize it"
