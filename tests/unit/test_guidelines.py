"""Tests for guideline generation utilities."""

import json
from unittest.mock import MagicMock, patch

import pytest

from altk_evolve.llm.guidelines import guidelines as guidelines_module
from altk_evolve.llm.guidelines.guidelines import generate_guidelines, parse_openai_agents_trajectory


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
