"""Tests for guideline generation utilities."""

from altk_evolve.config.guideline_runtime import GuidelineRuntime

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from jinja2 import Template

from altk_evolve.llm.guidelines import guidelines as guidelines_module
from altk_evolve.llm.guidelines.context import render_supporting_context
from altk_evolve.llm.guidelines.guidelines import generate_guidelines, parse_guideline_response, parse_openai_agents_trajectory
from altk_evolve.processing import TrajectoryOutcome
from altk_evolve.schema.guidelines import Guideline, SubtaskSegment, resolve_guideline_category


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
class TestCategoryRepair:
    """A category naming real categories in the wrong shape must not cost the generation.

    Validation is per response, so one off-contract label used to fail
    ``GuidelineGenerationResponse`` and discard every guideline mined from that trajectory.
    Only providers without constrained decoding get here; gpt-oss emits ``"strategy|recovery"``,
    which the prompts invite by spelling the choice as ``"strategy|recovery|optimization"``.
    """

    def test_multi_valued_category_keeps_the_whole_response(self, caplog):
        """The regression this exists for: the *sibling* guideline was the real loss."""
        multi = {**_GUIDELINE, "content": "Retry with a smaller page size", "category": "strategy|recovery"}
        with caplog.at_level(logging.INFO):
            guidelines = parse_guideline_response(json.dumps({"guidelines": [multi, _GUIDELINE]}), "standard")
        assert guidelines is not None
        assert [g.category for g in guidelines] == ["strategy", "strategy"]
        assert [g.content for g in guidelines] == ["Retry with a smaller page size", "Validate files before parsing"]
        assert "Repaired off-contract guideline category 'strategy|recovery'" in caplog.text

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("strategy|recovery", "strategy"),
            ("recovery|strategy", "recovery"),  # first named wins — not a fixed precedence
            ("strategy|recovery|optimization", "strategy"),  # the prompt placeholder, copied verbatim
            ("recovery, optimization", "recovery"),
            ("optimization / strategy", "optimization"),
            ("recovery or strategy", "recovery"),
            ("strategy and optimization", "strategy"),
            ("Strategy AND Recovery", "strategy"),  # the word separators are case-insensitive too
            ("recovery OR strategy", "recovery"),
            ("Recovery", "recovery"),
            ("  optimization  ", "optimization"),
            (["recovery", "strategy"], "recovery"),
            ('["strategy","recovery"]', "strategy"),  # a JSON array nested in the string field
            ("planning|recovery", "recovery"),  # skips a part that names nothing
        ],
    )
    def test_resolves_the_shapes_models_emit(self, raw, expected):
        assert resolve_guideline_category(raw) == expected

    @pytest.mark.parametrize("raw", ["not-a-real-category", "performance optimization work", "", "   ", None, 7, []])
    def test_returns_none_when_no_part_names_a_category(self, raw):
        """Including free text that merely mentions one — matching is on whole parts.

        None is what preserves ``test_returns_none_for_a_wrong_category_value``: an invented
        label means the model never picked from the choices, so it reaches the ``Literal`` and
        is rejected rather than being filed under a default that would hide it.
        """
        assert resolve_guideline_category(raw) is None

    def test_a_contract_category_is_not_reported_as_repaired(self, caplog):
        """Only off-contract output gets logged, so the signal stays worth watching."""
        with caplog.at_level(logging.INFO):
            Guideline(content="c", rationale="r", category="recovery", trigger="t")
        assert "guideline category" not in caplog.text


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
        monkeypatch.setattr("altk_evolve.config.evolve.evolve_config.segmentation_enabled", False)
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
        monkeypatch.setattr("altk_evolve.config.evolve.evolve_config.segmentation_enabled", False)
        mock_completion.return_value = _mock_completion_response([_GUIDELINE])

        with caplog.at_level(logging.INFO):
            results = generate_guidelines([{"role": "user", "content": "Fix CSV parsing"}])

        assert results[0].guidelines[0].content == "Validate files before parsing"
        # The context label is the only thing distinguishing the three pipelines in logs.
        assert "Recovered standard guideline response" in caplog.text
        assert "consistency" not in caplog.text


@pytest.mark.unit
class TestSegmentationFlag:
    """`EVOLVE_SEGMENTATION_ENABLED` must gate segmentation in both directions.

    The default value itself is pinned in tests/unit/test_evolve_config.py; these tests pin
    the *behaviour* on each side of the gate and set the flag explicitly, so they stay
    deterministic regardless of what the developer has in their environment.
    """

    # Five assistant turns (SEGMENTATION_MIN_STEPS, the shortest trajectory that is segmented),
    # each carrying a marker string that appears nowhere else (not in the prompt template, not
    # in a subtask description). Multi-step segments over this fixture are what make the
    # scoping assertions falsifiable: with single-step segments, "each segment got its own
    # steps" and "each segment got everything" are indistinguishable.
    MESSAGES = [
        {"role": "user", "content": "Find the retry settings for the acme service and summarize them"},
        {"role": "assistant", "content": "Listed the certificate directory to get my bearings."},
        {"role": "assistant", "content": "Opened the retry policy file that directory pointed to."},
        {"role": "assistant", "content": "Extracted the backoff ceiling it declares."},
        {"role": "assistant", "content": "Converted the jitter window to seconds."},
        {"role": "assistant", "content": "Reported the effective timeout to the caller."},
    ]
    SEG1_MARKERS = ("certificate directory", "retry policy file")
    SEG2_MARKERS = ("backoff ceiling", "jitter window", "effective timeout")

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

    @staticmethod
    def _prompts(mock_completion) -> list[str]:
        """The rendered prompt from each completion call, in call order."""
        return [c.kwargs["messages"][-1]["content"] for c in mock_completion.call_args_list]

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
        """Flag off: one LLM call that sees the whole trajectory, the segmenter is never reached,
        and task_description is the first user message verbatim. That last assertion is the
        documented cost of the default — task_description is both the clustering key
        (clustering.py) and the retrieval ranking key (retrieval.py), so it carries whatever
        the user typed, user-specific values included."""
        monkeypatch.setattr("altk_evolve.config.evolve.evolve_config.segmentation_enabled", False)
        mock_completion.return_value = _mock_completion_response(self.PAYLOAD)

        with patch("altk_evolve.llm.guidelines.segmentation.segment_trajectory") as mock_segment:
            results = generate_guidelines(self.MESSAGES)

        mock_segment.assert_not_called()
        mock_completion.assert_called_once()
        assert len(results) == 1
        assert results[0].task_description == "Find the retry settings for the acme service and summarize them"
        # The single call really does see every step, not a truncated view.
        prompt = self._prompts(mock_completion)[0]
        for m in self.SEG1_MARKERS + self.SEG2_MARKERS:
            assert m in prompt

    @patch("altk_evolve.llm.guidelines.guidelines.completion")
    @patch("altk_evolve.llm.guidelines.guidelines.supports_response_schema", return_value=True)
    @patch("altk_evolve.llm.guidelines.guidelines.get_supported_openai_params", return_value=["response_format"])
    def test_enabled_scopes_each_call_to_its_own_subtask_steps(
        self,
        _mock_params,
        _mock_schema,
        mock_completion,
        monkeypatch,
    ):
        """Flag on: the segmenter runs once over the raw messages, and each LLM call sees **only
        its own subtask's steps**.

        The step-scoping assertions are the point. Call count and description plumbing alone
        stay true even if every segment is handed the entire trajectory — a mutation that keeps
        all of segmentation's cost and removes all of its value. Scoping is also the load-bearing
        claim in the docstring and in docs/guides/configuration.md: a segment cannot see the
        correction in the segment next door, which is why segmentation can emit a guideline
        asserting an approach that a later step already superseded."""
        monkeypatch.setattr("altk_evolve.config.evolve.evolve_config.segmentation_enabled", True)
        mock_completion.return_value = _mock_completion_response(self.PAYLOAD)

        subtasks = [
            SubtaskSegment(
                generalized_description="Locate a configuration file on disk",
                purpose="Find the file to read",
                start_step=1,
                end_step=2,
            ),
            SubtaskSegment(
                generalized_description="Read the values a configuration file declares",
                purpose="Report the configured values",
                start_step=3,
                end_step=5,
            ),
        ]

        with patch("altk_evolve.llm.guidelines.segmentation.segment_trajectory", return_value=subtasks) as mock_segment:
            results = generate_guidelines(self.MESSAGES)

        mock_segment.assert_called_once_with(self.MESSAGES, options=GuidelineRuntime.from_settings())
        assert mock_completion.call_count == 2
        assert [r.task_description for r in results] == [s.generalized_description for s in subtasks]

        first, second = self._prompts(mock_completion)
        for m in self.SEG1_MARKERS:
            assert m in first, f"segment 1 prompt is missing its own step: {m}"
            assert m not in second, f"segment 2 prompt leaked a step from segment 1: {m}"
        for m in self.SEG2_MARKERS:
            assert m in second, f"segment 2 prompt is missing its own step: {m}"
            assert m not in first, f"segment 1 prompt leaked a step from segment 2: {m}"

    def _generate_with_subtasks(self, messages, subtasks, monkeypatch):
        """Run generate_guidelines with segmentation on and the segmenter stubbed."""
        monkeypatch.setattr("altk_evolve.config.evolve.evolve_config.segmentation_enabled", True)
        with (
            patch("altk_evolve.llm.guidelines.guidelines.completion") as mock_completion,
            patch("altk_evolve.llm.guidelines.guidelines.supports_response_schema", return_value=True),
            patch("altk_evolve.llm.guidelines.guidelines.get_supported_openai_params", return_value=["response_format"]),
            patch("altk_evolve.llm.guidelines.segmentation.segment_trajectory", return_value=subtasks) as mock_segment,
        ):
            mock_completion.return_value = _mock_completion_response(self.PAYLOAD)
            results = generate_guidelines(messages)
        return results, mock_segment, self._prompts(mock_completion)

    @staticmethod
    def _steps(n):
        return [{"role": "user", "content": "Do the task"}] + [{"role": "assistant", "content": f"marker step {i + 1}."} for i in range(n)]

    def test_trajectory_below_minimum_length_is_not_segmented(self, monkeypatch):
        """#293: a short trajectory is generated in one pass, like both consistency paths do.

        Before the gate, a one-step trajectory whose segmenter returned [1, 1] several times
        produced one generation call per copy of that single step."""
        from altk_evolve.llm.guidelines.segmentation import SEGMENTATION_MIN_STEPS

        repeated = [SubtaskSegment(generalized_description=f"Subtask {i}", purpose="p", start_step=1, end_step=1) for i in range(5)]
        for n in (1, SEGMENTATION_MIN_STEPS - 1):
            results, mock_segment, prompts = self._generate_with_subtasks(self._steps(n), repeated, monkeypatch)
            mock_segment.assert_not_called()
            assert len(prompts) == 1
            assert [r.task_description for r in results] == ["Do the task"]

    def test_repeated_subtask_range_is_generated_once(self, monkeypatch):
        subtasks = [
            SubtaskSegment(generalized_description="First half", purpose="p", start_step=1, end_step=3),
            SubtaskSegment(generalized_description="First half again", purpose="p", start_step=1, end_step=3),
            SubtaskSegment(generalized_description="Second half", purpose="p", start_step=4, end_step=6),
        ]
        results, _, prompts = self._generate_with_subtasks(self._steps(6), subtasks, monkeypatch)
        assert [r.task_description for r in results] == ["First half", "Second half"]
        assert "marker step 1." in prompts[0] and "marker step 4." not in prompts[0]
        assert "marker step 4." in prompts[1] and "marker step 1." not in prompts[1]

    def test_ranges_that_clamp_to_the_same_slice_count_once(self, monkeypatch):
        """[1, 6] and [1, 9] are the same slice of a 6-step trajectory; with one distinct
        slice left, generation falls back to a single whole-trajectory call."""
        subtasks = [
            SubtaskSegment(generalized_description="Everything", purpose="p", start_step=1, end_step=6),
            SubtaskSegment(generalized_description="Everything, overshooting", purpose="p", start_step=1, end_step=9),
        ]
        results, _, prompts = self._generate_with_subtasks(self._steps(6), subtasks, monkeypatch)
        assert len(prompts) == 1
        assert [r.task_description for r in results] == ["Everything"]

    @pytest.mark.parametrize(
        "ranges",
        [
            pytest.param([(1, 3)], id="returned-once"),
            pytest.param([(1, 3), (1, 3)], id="returned-twice"),
            pytest.param([(1, 3), (9, 9)], id="with-an-out-of-range-subtask"),
        ],
    )
    def test_one_distinct_range_keeps_its_generalized_description(self, ranges, monkeypatch):
        """The one-pass call is keyed the same way whether the segmenter returned a range once
        or repeated it; dedupe must not swap a generalized key for the raw user message."""
        subtasks = [
            SubtaskSegment(generalized_description=f"Kept {i}", purpose="p", start_step=a, end_step=b) for i, (a, b) in enumerate(ranges)
        ]
        results, _, prompts = self._generate_with_subtasks(self._steps(6), subtasks, monkeypatch)
        assert len(prompts) == 1
        assert [r.task_description for r in results] == ["Kept 0"]
        assert "marker step 1." in prompts[0] and "marker step 6." in prompts[0]

    @pytest.mark.parametrize(
        "ranges",
        [
            pytest.param([], id="no-subtasks"),
            pytest.param([(9, 9)], id="one-out-of-range"),
            pytest.param([(7, 9), (8, 8)], id="all-out-of-range"),
        ],
    )
    def test_no_usable_range_uses_the_first_user_message(self, ranges, monkeypatch):
        subtasks = [
            SubtaskSegment(generalized_description=f"Unusable {i}", purpose="p", start_step=a, end_step=b)
            for i, (a, b) in enumerate(ranges)
        ]
        results, _, prompts = self._generate_with_subtasks(self._steps(6), subtasks, monkeypatch)
        assert len(prompts) == 1
        assert [r.task_description for r in results] == ["Do the task"]

    def test_length_gate_counts_parsed_steps_not_assistant_messages(self, monkeypatch):
        """Three assistant messages with two native tool calls each parse into six steps, so
        the trajectory clears SEGMENTATION_MIN_STEPS and is segmented."""
        messages: list[dict] = [{"role": "user", "content": "Do the task"}]
        for i in range(3):
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": f"c{i}", "type": "function", "function": {"name": f"act_{i}", "arguments": "{}"}},
                        {"id": f"d{i}", "type": "function", "function": {"name": f"also_{i}", "arguments": "{}"}},
                    ],
                }
            )
            messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
        subtasks = [
            SubtaskSegment(generalized_description="First half", purpose="p", start_step=1, end_step=3),
            SubtaskSegment(generalized_description="Second half", purpose="p", start_step=4, end_step=6),
        ]
        results, mock_segment, prompts = self._generate_with_subtasks(messages, subtasks, monkeypatch)
        mock_segment.assert_called_once()
        assert [r.task_description for r in results] == ["First half", "Second half"]
        assert "act_0" in prompts[0] and "act_2" not in prompts[0]
        assert "act_2" in prompts[1] and "act_0" not in prompts[1]

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
        monkeypatch.setattr("altk_evolve.config.evolve.evolve_config.segmentation_enabled", True)
        mock_completion.return_value = _mock_completion_response(self.PAYLOAD)

        with patch(
            "altk_evolve.llm.guidelines.segmentation.segment_trajectory",
            side_effect=RuntimeError("segmenter unavailable"),
        ) as mock_segment:
            results = generate_guidelines(self.MESSAGES)

        # Otherwise the length gate alone could produce this result, and the error path untested.
        mock_segment.assert_called_once()
        mock_completion.assert_called_once()
        assert len(results) == 1
        assert results[0].task_description == "Find the retry settings for the acme service and summarize them"


_PRE_OUTCOME_TEMPLATE = Path(__file__).parents[1] / "fixtures" / "generate_guidelines_pre_outcome.jinja2"


@pytest.mark.unit
class TestOutcomePrompt:
    """The outcome-grounded prompt is used only when a trajectory carries an outcome."""

    MESSAGES = [
        {"role": "user", "content": "Refund the duplicate charge on the latest order"},
        {"role": "assistant", "content": "Refunded the most recent charge without comparing amounts."},
    ]

    @staticmethod
    def _prompt(mock_completion) -> str:
        return str(mock_completion.call_args.kwargs["messages"][-1]["content"])

    @pytest.fixture(autouse=True)
    def _llm(self, monkeypatch):
        monkeypatch.setattr("altk_evolve.config.evolve.evolve_config.segmentation_enabled", False)
        with (
            patch("altk_evolve.llm.guidelines.guidelines.completion") as mock_completion,
            patch("altk_evolve.llm.guidelines.guidelines.supports_response_schema", return_value=True) as mock_schema,
            patch("altk_evolve.llm.guidelines.guidelines.get_supported_openai_params", return_value=["response_format"]),
        ):
            mock_completion.return_value = _mock_completion_response({"guidelines": [_GUIDELINE]})
            self.mock_completion, self.mock_schema = mock_completion, mock_schema
            yield

    @pytest.mark.parametrize("constrained", [True, False])
    @pytest.mark.parametrize("context_messages", [None, [{"role": "user", "content": "Earlier request"}]])
    def test_without_outcome_prompt_is_unchanged(self, constrained, context_messages):
        """Pins the default path: callers that pass no outcome get the pre-outcome prompt byte for byte."""
        self.mock_schema.return_value = constrained
        generate_guidelines(self.MESSAGES, context_messages=context_messages)

        data = parse_openai_agents_trajectory(self.MESSAGES, context_messages=context_messages)
        expected = Template(_PRE_OUTCOME_TEMPLATE.read_text()).render(
            task_instruction=data["task_instruction"],
            supporting_context=render_supporting_context(context_messages),
            num_steps=data["num_steps"],
            trajectory_summary=data["trajectory_summary"],
            constrained_decoding_supported=constrained,
        )
        assert self._prompt(self.mock_completion) == expected

    def test_failure_renders_status_failed_checks_and_detail(self):
        outcome = TrajectoryOutcome(success=False, failed_checks=("refund matches duplicate amount",), detail="Expected 19.99")
        generate_guidelines(self.MESSAGES, outcome=outcome)

        prompt = self._prompt(self.mock_completion)
        assert "**Task Status:** FAILED, as judged by an external evaluation" in prompt
        assert "- refund matches duplicate amount\n" in prompt
        assert "**Evaluation detail:**\nExpected 19.99\n" in prompt
        assert "Return 1 to 3 corrective guidelines that would have prevented this failure, grounded in" in prompt
        assert "An empty list is better than generic advice" in prompt
        assert "leave out task IDs" in prompt
        assert "There is no evaluation" not in prompt
        assert "Consider positive patterns" not in prompt
        assert "did not contribute to the failure" not in prompt

    def test_success_asks_only_for_non_obvious_patterns(self):
        generate_guidelines(self.MESSAGES, outcome=TrajectoryOutcome(success=True))

        prompt = self._prompt(self.mock_completion)
        assert "**Task Status:** SUCCEEDED" in prompt
        assert "Return at most 2 guidelines" in prompt
        assert "If the agent succeeded without struggle, return an empty list." in prompt
        assert "Failed checks" not in prompt
        assert "Evaluation detail" not in prompt
        assert "corrective" not in prompt

    def test_failure_without_checks_or_detail_is_not_told_to_ground_in_them(self):
        generate_guidelines(self.MESSAGES, outcome=TrajectoryOutcome(success=False))

        prompt = self._prompt(self.mock_completion)
        assert "Return 1 to 3 corrective guidelines that would have prevented this failure.\n" in prompt
        assert "grounded in" not in prompt

    def test_outcome_inputs_are_bounded(self):
        checks = tuple(f"check {i}" for i in range(12))
        generate_guidelines(self.MESSAGES, outcome=TrajectoryOutcome(success=False, failed_checks=checks, detail="x" * 3000))

        prompt = self._prompt(self.mock_completion)
        assert "- check 9\n" in prompt
        assert "check 10" not in prompt
        assert "- ... and 2 more\n" in prompt
        assert "x" * 2500 + "\n... [truncated]" in prompt
        assert "x" * 2501 not in prompt

    def test_each_failed_check_is_bounded(self):
        checks = ("y" * 600, "short check")
        generate_guidelines(self.MESSAGES, outcome=TrajectoryOutcome(success=False, failed_checks=checks))

        prompt = self._prompt(self.mock_completion)
        assert "- " + "y" * 500 + " ... [truncated]\n" in prompt
        assert "y" * 501 not in prompt
        assert "- short check\n" in prompt

    @pytest.mark.parametrize(("success", "limit"), [(False, 3), (True, 2)])
    def test_outcome_limits_are_enforced(self, success, limit, caplog):
        payload = {"guidelines": [{**_GUIDELINE, "content": f"guideline {i}"} for i in range(5)]}
        self.mock_completion.return_value = _mock_completion_response(payload)

        with caplog.at_level(logging.INFO):
            results = generate_guidelines(self.MESSAGES, outcome=TrajectoryOutcome(success=success))

        assert [g.content for g in results[0].guidelines] == [f"guideline {i}" for i in range(limit)]
        assert f"Keeping the first {limit} of 5" in caplog.text

    @pytest.mark.parametrize("success", [False, True])
    def test_outcome_prompt_keeps_the_quality_threshold(self, success):
        """The outcome rules replace the no-outcome rules, not the quality threshold that follows them."""
        generate_guidelines(self.MESSAGES, outcome=TrajectoryOutcome(success=success))

        prompt = self._prompt(self.mock_completion)
        assert "\n\n# Quality threshold\n" in prompt
        assert "Self-reported success is not evidence of a successful operation." in prompt
        assert 'If nothing meets this threshold, return {"guidelines": []}.' in prompt

    def test_success_with_nothing_new_returns_no_guidelines(self):
        self.mock_completion.return_value = _mock_completion_response({"guidelines": []})

        results = generate_guidelines(self.MESSAGES, outcome=TrajectoryOutcome(success=True))

        assert results[0].guidelines == []

    def test_every_segment_receives_the_outcome(self, monkeypatch):
        monkeypatch.setattr("altk_evolve.config.evolve.evolve_config.segmentation_enabled", True)
        # Five steps, so the trajectory clears the segmentation length gate (SEGMENTATION_MIN_STEPS).
        checks = [{"role": "assistant", "content": f"Checked charge {i}."} for i in range(3)]
        messages = [*self.MESSAGES, *checks, {"role": "assistant", "content": "Reported the refund."}]
        subtasks = [
            SubtaskSegment(generalized_description="Issue a refund", purpose="Refund", start_step=1, end_step=4),
            SubtaskSegment(generalized_description="Report the result", purpose="Report", start_step=5, end_step=5),
        ]

        with patch("altk_evolve.llm.guidelines.segmentation.segment_trajectory", return_value=subtasks):
            generate_guidelines(messages, outcome=TrajectoryOutcome(success=False, failed_checks=("refund amount",)))

        prompts = [c.kwargs["messages"][-1]["content"] for c in self.mock_completion.call_args_list]
        assert len(prompts) == 2
        assert all("**Task Status:** FAILED" in p and "- refund amount\n" in p for p in prompts)
        # A segment that had nothing to do with the failure may return nothing.
        allows_empty = "evaluation detail above. If this part of the trajectory did not contribute to the failure, return an empty list.\n"
        assert all(allows_empty in p for p in prompts)
