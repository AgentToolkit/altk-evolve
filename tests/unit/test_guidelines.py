"""Tests for guideline generation utilities."""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from altk_evolve.llm.guidelines import guidelines as guidelines_module
from altk_evolve.llm.guidelines.guidelines import generate_guidelines, parse_guideline_response, parse_openai_agents_trajectory


def _mock_completion_response(payload: dict | list) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    return response


# One valid guideline, as the schema requires it.
_GUIDELINE = {
    "content": "Validate files before parsing",
    "rationale": "Avoids parser crashes on empty inputs",
    "category": "strategy",
    "trigger": "Before reading user-provided CSV files",
}


@pytest.mark.unit
class TestParseGuidelineResponse:
    """Repairs for the two ways models break the {"guidelines": [...]} output contract."""

    def test_returns_guidelines_for_well_formed_response(self, caplog):
        with caplog.at_level(logging.INFO):
            guidelines = parse_guideline_response(json.dumps({"guidelines": [_GUIDELINE]}), "standard")
        assert guidelines is not None
        assert guidelines[0].content == "Validate files before parsing"
        # Nothing was repaired, so nothing should be reported as repaired.
        assert "after repair" not in caplog.text

    def test_wraps_bare_array(self):
        """A bare [...] parses as valid JSON but fails validation — wrap it under the key."""
        guidelines = parse_guideline_response(json.dumps([_GUIDELINE]), "standard")
        assert guidelines is not None
        assert guidelines[0].content == "Validate files before parsing"

    def test_repairs_lone_backslashes(self):
        """LaTeX-style \\( \\) in a string value is not a valid JSON escape and fails to parse."""
        raw = r'{"guidelines": [{"content": "Write \( x \) inline", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        guidelines = parse_guideline_response(raw, "consistency")
        assert guidelines is not None
        assert guidelines[0].content == r"Write \( x \) inline"

    def test_repairs_compose_for_bare_array_with_lone_backslashes(self):
        """Both malformations at once — the escape repair must feed into the wrap repair."""
        raw = r'[{"content": "Write \( x \) inline", "rationale": "r", "category": "strategy", "trigger": "t"}]'
        guidelines = parse_guideline_response(raw, "fast consistency")
        assert guidelines is not None
        assert guidelines[0].content == r"Write \( x \) inline"

    def test_repairs_a_backslash_u_that_is_not_a_unicode_escape(self):
        r"""\u is only a JSON escape when four hex digits follow. LaTeX like \underbrace
        starts with \u but is invalid JSON, so it must be escaped rather than skipped —
        skipping it left the response unparseable and the whole generation discarded."""
        raw = r'{"guidelines": [{"content": "use \underbrace{x}", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        guidelines = parse_guideline_response(raw, "consistency")
        assert guidelines is not None
        assert guidelines[0].content == r"use \underbrace{x}"

    def test_preserves_a_real_unicode_escape_while_repairing_another_escape(self):
        r"""A response carrying both a valid é and an invalid \( must repair only the
        latter — the complete escape still has to decode to its character. The payload uses
        the escape sequence, not a literal é, so this fails if the u[0-9a-fA-F]{4} exclusion
        is ever dropped and \u starts being escaped unconditionally."""
        raw = r'{"guidelines": [{"content": "caf\u00e9 and \( x \)", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        guidelines = parse_guideline_response(raw, "consistency")
        assert guidelines is not None
        assert guidelines[0].content == "café and " + r"\( x \)"

    def test_preserves_valid_control_escapes_while_repairing(self):
        r"""Single-character escapes such as \n are valid JSON and must keep decoding to
        their control character, not be turned into a literal backslash-n."""
        raw = r'{"guidelines": [{"content": "line1\nline2 \( x \)", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        guidelines = parse_guideline_response(raw, "consistency")
        assert guidelines is not None
        assert guidelines[0].content == "line1\nline2 " + r"\( x \)"

    def test_logs_the_repairs_that_were_applied(self, caplog):
        raw = r'[{"content": "Write \( x \) inline", "rationale": "r", "category": "strategy", "trigger": "t"}]'
        with caplog.at_level(logging.INFO):
            parse_guideline_response(raw, "fast consistency")
        assert "Recovered fast consistency guideline response after repair" in caplog.text
        assert "escaped lone backslashes" in caplog.text
        assert 'wrapped a bare array under "guidelines"' in caplog.text

    def test_returns_none_for_unparseable_response(self, caplog):
        guidelines = parse_guideline_response("not json at all {{{", "standard")
        assert guidelines is None
        assert "Failed to parse standard guideline response" in caplog.text

    def test_returns_none_when_array_items_do_not_match_the_schema(self, caplog):
        """A bare array is only rescued when its items are valid guidelines."""
        guidelines = parse_guideline_response(json.dumps([{"content": "no other required fields"}]), "standard")
        assert guidelines is None
        assert "Failed to parse standard guideline response" in caplog.text

    def test_returns_none_for_a_wrong_category_value(self, caplog):
        """category is a Literal, so an unrecognised value must not be quietly accepted."""
        bad = {**_GUIDELINE, "category": "not-a-real-category"}
        guidelines = parse_guideline_response(json.dumps({"guidelines": [bad]}), "standard")
        assert guidelines is None
        assert "Failed to parse standard guideline response" in caplog.text


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

    @patch("altk_evolve.llm.guidelines.guidelines.completion")
    @patch("altk_evolve.llm.guidelines.guidelines.supports_response_schema", return_value=True)
    @patch("altk_evolve.llm.guidelines.guidelines.get_supported_openai_params", return_value=["response_format"])
    def test_generate_guidelines_recovers_a_bare_array_response(
        self,
        _mock_params,
        _mock_schema,
        mock_completion,
        monkeypatch,
    ):
        """The standard pipeline is wired to the repairing parser, not a strict one."""
        monkeypatch.setattr(guidelines_module.evolve_config, "segmentation_enabled", False)
        mock_completion.return_value = _mock_completion_response([_GUIDELINE])

        results = generate_guidelines([{"role": "user", "content": "Fix CSV parsing"}])

        assert results[0].guidelines[0].content == "Validate files before parsing"
