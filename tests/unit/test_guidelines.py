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

    def test_valid_control_escapes_are_honoured_when_no_repair_is_needed(self):
        r"""A response that parses on its own is never touched — \n keeps decoding to a
        newline. The repair path is the only thing that treats escapes with suspicion."""
        raw = r'{"guidelines": [{"content": "line1\nline2", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        guidelines = parse_guideline_response(raw, "consistency")
        assert guidelines is not None
        assert guidelines[0].content == "line1\nline2"

    def test_discards_when_the_repair_would_inject_control_characters(self, caplog):
        r"""A model emitting raw backslashes emits them throughout, so \t in C:\trainer meant
        a literal backslash, not a tab. Decoding it anyway yields a plausible-looking
        guideline with silently corrupted text, which then gets embedded and served on — so
        fail closed instead of reporting a successful recovery."""
        raw = r'{"guidelines": [{"content": "Normalize C:\Users\trainer\runs. Bound \( x \)", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        with caplog.at_level(logging.WARNING):
            guidelines = parse_guideline_response(raw, "standard")
        assert guidelines is None
        assert "single-backslash" in caplog.text
        # Must not be reported as a success.
        assert "Recovered" not in caplog.text

    @pytest.mark.parametrize("indent", ["\n  ", "\n\t"])
    def test_discards_corruption_in_a_pretty_printed_response(self, indent):
        r"""The decision must come from the string literals, not from the document's
        characters. A pretty-printed or tab-indented response carries literal newlines and
        tabs *between* tokens, so a document-wide character scan would whitelist \n and \t
        for every value and let exactly this corruption through — while the identical
        single-line response was rejected. Scanning inside literals sees neither."""
        raw = (
            "{" + indent + '"guidelines": [' + indent + '  {"content": "Write to C:\\new\\data", '
            '"rationale": "r", "category": "strategy", "trigger": "t"}' + indent + "]" + "\n}"
        )
        assert parse_guideline_response(raw, "standard") is None

    def test_an_intended_control_escape_is_discarded_alongside_a_sibling_needing_repair(self):
        r"""The accepted cost of failing closed, pinned so the trade-off is on the record.

        An intended \t is *indistinguishable* from a misread one. These two literals are the
        same shape — one valid \t escape that the repair never touches:

            "Emit rows as name\tvalue"      intended a tab
            "Open C:\temp"                  intended a backslash

        Nothing in the decoded value separates them either: the escape that corrupts consumes
        its own backslash, so no backslash survives to key on. Requiring a surviving backslash
        instead accepted *both*, which let the second be embedded with a tab spliced into it
        and logged as a successful recovery.

        So the whole response is discarded whenever the backslash repair ran and any literal
        still carries a single-backslash control escape. A discarded response is regenerable;
        corrupted text served into the entity store is not.
        """
        raw = (
            '{"guidelines": ['
            '{"content": "Emit rows as name\\tvalue", "rationale": "TSV", "category": "optimization", "trigger": "t"}, '
            '{"content": "State bounds as \\( n \\le 10 \\)", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        )
        assert parse_guideline_response(raw, "standard") is None

    def test_an_intended_control_escape_survives_when_no_repair_was_needed(self):
        r"""The scope that keeps failing closed tolerable: only a response that needed the
        backslash repair is suspect, because that is the evidence the model was not escaping
        backslashes. A well-formed response keeps its \t, sibling LaTeX and all — the LaTeX
        here is correctly escaped, so no repair runs."""
        raw = (
            '{"guidelines": ['
            '{"content": "Emit rows as name\\tvalue", "rationale": "TSV", "category": "optimization", "trigger": "t"}, '
            '{"content": "State bounds as \\\\( n \\\\le 10 \\\\)", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        )
        guidelines = parse_guideline_response(raw, "standard")
        assert guidelines is not None
        assert guidelines[0].content == "Emit rows as name\tvalue"
        assert guidelines[1].content == r"State bounds as \( n \le 10 \)"

    @pytest.mark.parametrize(
        "content,corrupted_as",
        [
            (r"Write to C:\newdir", "Write to C:\newdir"),
            (r"Open C:\temp", "Open C:\temp"),
            (r"Strip \r from input", "Strip \r from input"),
            # \b and \f are equally ordinary path starts, and equally ambiguous
            (r"Install to C:\bin", "Install to C:\bin"),
            (r"Scan C:\files first", "Scan C:\files first"),
        ],
    )
    def test_discards_a_single_backslash_path_or_regex(self, content, corrupted_as, caplog):
        r"""The fail-open this guard exists to close. Each of these has exactly one backslash,
        and the escape consumes it — so after decoding there is no backslash left to detect,
        and requiring one waved them all through with a control character spliced in. Windows
        paths and regex escapes are the common shapes, so this is the case that matters."""
        raw = '{"guidelines": [{"content": "%s", "rationale": "bound \\( x \\)", "category": "strategy", "trigger": "t"}]}' % content
        with caplog.at_level(logging.INFO):
            guidelines = parse_guideline_response(raw, "standard")

        assert guidelines is None, f"accepted with corruption: {corrupted_as!r}"
        assert "Recovered" not in caplog.text, "a fail-open must not be reported as a success"

    def test_a_correctly_escaped_backslash_before_t_is_not_read_as_a_tab(self):
        r"""``\\t`` decodes to a backslash and a ``t``, never a tab, so it must survive even
        in a repaired response. The scan has to *consume* the ``\\`` pair: stepping one
        character at a time would re-read its second backslash as the start of ``\t`` and
        discard a response that was correctly escaped all along."""
        raw = r'{"guidelines": [{"content": "path \\temp and \( x \)", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        guidelines = parse_guideline_response(raw, "standard")
        assert guidelines is not None
        assert guidelines[0].content == r"path \temp and \( x \)"
        assert "\t" not in guidelines[0].content, "no tab may be spliced in"

    def test_a_spelled_out_unicode_escape_is_not_ambiguous(self):
        r"""\u0009 encodes a tab deliberately — that is not what failing to escape a path
        looks like — so it survives even in a response the repair ran on. Only the
        single-backslash forms are ambiguous."""
        raw = r'{"guidelines": [{"content": "Use \u0009 then \( x \)", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        guidelines = parse_guideline_response(raw, "standard")
        assert guidelines is not None
        assert guidelines[0].content == "Use \t then " + r"\( x \)"

    def test_repair_survives_a_correctly_escaped_backslash(self):
        r"""A response mixing a correct \\ with a raw \( is realistic. The escape scan has to
        *consume* the valid pair: looking past it re-examined the second backslash as a new
        escape, turned \\d into \\\d, and discarded every guideline in the batch."""
        raw = r'{"guidelines": [{"content": "match \\d+ and \( x \)", "rationale": "r", "category": "strategy", "trigger": "t"}]}'
        guidelines = parse_guideline_response(raw, "consistency")
        assert guidelines is not None
        assert guidelines[0].content == r"match \d+ and \( x \)"

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
        caplog,
    ):
        """The standard pipeline is wired to the repairing parser, not a strict one."""
        monkeypatch.setattr(guidelines_module.evolve_config, "segmentation_enabled", False)
        mock_completion.return_value = _mock_completion_response([_GUIDELINE])

        with caplog.at_level(logging.INFO):
            results = generate_guidelines([{"role": "user", "content": "Fix CSV parsing"}])

        assert results[0].guidelines[0].content == "Validate files before parsing"
        # The context label is the only thing distinguishing the three pipelines in logs.
        assert "Recovered standard guideline response" in caplog.text
        assert "consistency" not in caplog.text
