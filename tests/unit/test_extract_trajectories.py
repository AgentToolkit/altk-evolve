"""Tests for extract_trajectories.py standalone script functions."""

import json
import os
import sys
from unittest.mock import MagicMock, patch, Mock

import pytest

# Add project root to path for importing the standalone script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from extract_trajectories import (
    parse_content,
    extract_messages_from_span,
    convert_anthropic_to_openai,
    extract_trajectory,
    filter_system_reminders,
    clean_trajectory,
    format_trajectory_as_text,
    get_trajectories,
)

# Mark all tests in this module as phoenix tests (skipped by default)
pytestmark = pytest.mark.phoenix


# =============================================================================
# parse_content() Tests
# =============================================================================


@pytest.mark.unit
class TestParseContent:
    """Tests for parse_content function."""

    def test_parse_content_json_dict(self):
        """Test parsing JSON dict string."""
        content = '{"key": "value"}'
        result = parse_content(content)
        assert result == {"key": "value"}

    def test_parse_content_json_list(self):
        """Test parsing JSON list string."""
        content = '[{"type": "text", "text": "hello"}]'
        result = parse_content(content)
        assert result == [{"type": "text", "text": "hello"}]

    def test_parse_content_python_literal_dict(self):
        """Test parsing Python literal dict (single quotes)."""
        content = "{'key': 'value'}"
        result = parse_content(content)
        assert result == {"key": "value"}

    def test_parse_content_python_literal_list(self):
        """Test parsing Python literal list."""
        content = "[{'type': 'text'}]"
        result = parse_content(content)
        assert result == [{"type": "text"}]

    def test_parse_content_plain_string(self):
        """Test plain string is returned unchanged."""
        content = "Just a plain message"
        result = parse_content(content)
        assert result == "Just a plain message"

    def test_parse_content_passthrough_dict(self):
        """Test dict is passed through."""
        content = {"already": "dict"}
        result = parse_content(content)
        assert result == {"already": "dict"}

    def test_parse_content_passthrough_list(self):
        """Test list is passed through."""
        content = [1, 2, 3]
        result = parse_content(content)
        assert result == [1, 2, 3]

    def test_parse_content_invalid_syntax(self):
        """Test invalid syntax returns original string."""
        content = "not {valid json"
        result = parse_content(content)
        assert result == content


# =============================================================================
# extract_messages_from_span() Tests
# =============================================================================


@pytest.mark.unit
class TestExtractMessagesFromSpan:
    """Tests for extract_messages_from_span function."""

    def test_extract_single_prompt(self):
        """Test extracting a single prompt."""
        span = {
            "attributes": {
                "gen_ai.prompt.0.role": "user",
                "gen_ai.prompt.0.content": "Hello"
            }
        }
        messages = extract_messages_from_span(span)
        assert len(messages) == 1
        assert messages[0] == {
            "index": 0,
            "type": "prompt",
            "role": "user",
            "content": "Hello"
        }

    def test_extract_multiple_prompts_sorted(self):
        """Test that multiple prompts are sorted by index."""
        span = {
            "attributes": {
                "gen_ai.prompt.2.role": "user",
                "gen_ai.prompt.2.content": "Third",
                "gen_ai.prompt.0.role": "system",
                "gen_ai.prompt.0.content": "First",
                "gen_ai.prompt.1.role": "user",
                "gen_ai.prompt.1.content": "Second"
            }
        }
        messages = extract_messages_from_span(span)
        assert len(messages) == 3
        assert messages[0]["content"] == "First"
        assert messages[1]["content"] == "Second"
        assert messages[2]["content"] == "Third"

    def test_extract_completion_messages(self):
        """Test extracting completion messages."""
        span = {
            "attributes": {
                "gen_ai.completion.0.role": "assistant",
                "gen_ai.completion.0.content": "I can help with that."
            }
        }
        messages = extract_messages_from_span(span)
        assert len(messages) == 1
        assert messages[0]["type"] == "completion"
        assert messages[0]["role"] == "assistant"

    def test_extract_mixed_prompts_and_completions(self):
        """Test extracting both prompts and completions."""
        span = {
            "attributes": {
                "gen_ai.prompt.0.role": "user",
                "gen_ai.prompt.0.content": "Question",
                "gen_ai.completion.0.role": "assistant",
                "gen_ai.completion.0.content": "Answer"
            }
        }
        messages = extract_messages_from_span(span)
        prompts = [m for m in messages if m["type"] == "prompt"]
        completions = [m for m in messages if m["type"] == "completion"]
        assert len(prompts) == 1
        assert len(completions) == 1

    def test_extract_empty_attributes(self):
        """Test extracting from span with no relevant attributes."""
        span = {"attributes": {"other.attr": "value"}}
        messages = extract_messages_from_span(span)
        assert messages == []

    def test_extract_parses_json_content(self):
        """Test that JSON content is parsed."""
        span = {
            "attributes": {
                "gen_ai.prompt.0.role": "assistant",
                "gen_ai.prompt.0.content": '[{"type": "text", "text": "Hi"}]'
            }
        }
        messages = extract_messages_from_span(span)
        assert messages[0]["content"] == [{"type": "text", "text": "Hi"}]

    def test_extract_skips_missing_role(self):
        """Test that entries without role are skipped."""
        span = {
            "attributes": {
                "gen_ai.prompt.0.content": "No role here"
            }
        }
        messages = extract_messages_from_span(span)
        assert messages == []


# =============================================================================
# convert_anthropic_to_openai() Tests
# =============================================================================


@pytest.mark.unit
class TestConvertAnthropicToOpenai:
    """Tests for convert_anthropic_to_openai function."""

    def test_convert_string_content(self):
        """Test converting simple string content."""
        result = convert_anthropic_to_openai("Hello", "user")
        assert result == {"role": "user", "content": "Hello"}

    def test_convert_text_block(self):
        """Test converting text block."""
        content = [{"type": "text", "text": "Hello world"}]
        result = convert_anthropic_to_openai(content, "assistant")
        assert result["role"] == "assistant"
        assert result["content"] == "Hello world"

    def test_convert_thinking_block(self):
        """Test converting thinking block."""
        content = [
            {"type": "thinking", "thinking": "Let me think about this..."},
            {"type": "text", "text": "Here's my answer."}
        ]
        result = convert_anthropic_to_openai(content, "assistant")
        assert result["thinking"] == "Let me think about this..."
        assert result["content"] == "Here's my answer."

    def test_convert_multiple_thinking_blocks(self):
        """Test converting multiple thinking blocks."""
        content = [
            {"type": "thinking", "thinking": "First thought"},
            {"type": "thinking", "thinking": "Second thought"},
            {"type": "text", "text": "Final answer"}
        ]
        result = convert_anthropic_to_openai(content, "assistant")
        assert "First thought" in result["thinking"]
        assert "Second thought" in result["thinking"]

    def test_convert_tool_use_block(self):
        """Test converting tool_use block."""
        content = [
            {
                "type": "tool_use",
                "id": "tool_abc",
                "name": "search",
                "input": {"query": "test"}
            }
        ]
        result = convert_anthropic_to_openai(content, "assistant")
        assert result["role"] == "assistant"
        assert len(result["tool_calls"]) == 1
        tool_call = result["tool_calls"][0]
        assert tool_call["id"] == "tool_abc"
        assert tool_call["type"] == "function"
        assert tool_call["function"]["name"] == "search"
        assert json.loads(tool_call["function"]["arguments"]) == {"query": "test"}

    def test_convert_multiple_tool_use_blocks(self):
        """Test converting multiple tool_use blocks."""
        content = [
            {"type": "tool_use", "id": "t1", "name": "read", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "write", "input": {}}
        ]
        result = convert_anthropic_to_openai(content, "assistant")
        assert len(result["tool_calls"]) == 2

    def test_convert_tool_result_block(self):
        """Test converting tool_result block."""
        content = [
            {
                "type": "tool_result",
                "tool_use_id": "tool_123",
                "content": "Result data",
                "is_error": False
            }
        ]
        result = convert_anthropic_to_openai(content, "user")
        assert result["role"] == "tool"
        assert "tool_results" in result
        assert result["tool_results"][0]["tool_call_id"] == "tool_123"
        assert result["tool_results"][0]["content"] == "Result data"

    def test_convert_tool_result_with_error(self):
        """Test converting tool_result with error flag."""
        content = [
            {
                "type": "tool_result",
                "tool_use_id": "tool_err",
                "content": "Error occurred",
                "is_error": True
            }
        ]
        result = convert_anthropic_to_openai(content, "user")
        assert result["tool_results"][0]["is_error"] is True

    def test_convert_filters_no_content_text(self):
        """Test that (no content) placeholder is filtered."""
        content = [
            {"type": "text", "text": "(no content)"},
            {"type": "text", "text": "Real text"}
        ]
        result = convert_anthropic_to_openai(content, "assistant")
        assert result["content"] == "Real text"

    def test_convert_filters_empty_text(self):
        """Test that empty text is filtered."""
        content = [
            {"type": "text", "text": ""},
            {"type": "text", "text": "Content"}
        ]
        result = convert_anthropic_to_openai(content, "assistant")
        assert result["content"] == "Content"

    def test_convert_assistant_tool_only_no_content(self):
        """Test assistant with only tool calls has null content."""
        content = [
            {"type": "tool_use", "id": "t1", "name": "test", "input": {}}
        ]
        result = convert_anthropic_to_openai(content, "assistant")
        assert result.get("content") is None
        assert "tool_calls" in result

    def test_convert_non_dict_in_list(self):
        """Test handling non-dict items in content list."""
        content = ["string item", {"type": "text", "text": "dict item"}]
        result = convert_anthropic_to_openai(content, "user")
        assert "string item" in result["content"]
        assert "dict item" in result["content"]

    def test_convert_non_list_non_string(self):
        """Test converting non-list, non-string content."""
        result = convert_anthropic_to_openai(42, "user")
        assert result == {"role": "user", "content": "42"}

    def test_convert_user_regular_message(self):
        """Test converting regular user message (not tool result)."""
        content = [{"type": "text", "text": "User question"}]
        result = convert_anthropic_to_openai(content, "user")
        assert result["role"] == "user"
        assert result["content"] == "User question"


# =============================================================================
# filter_system_reminders() Tests
# =============================================================================


@pytest.mark.unit
class TestFilterSystemReminders:
    """Tests for filter_system_reminders function."""

    def test_filter_single_reminder(self):
        """Test filtering a single system reminder."""
        text = "Before <system-reminder>reminder</system-reminder> after"
        result = filter_system_reminders(text)
        assert result == "Before  after"

    def test_filter_multiple_reminders(self):
        """Test filtering multiple system reminders."""
        text = "<system-reminder>first</system-reminder>middle<system-reminder>second</system-reminder>"
        result = filter_system_reminders(text)
        assert result == "middle"

    def test_filter_multiline_reminder(self):
        """Test filtering multiline system reminder."""
        text = "Start\n<system-reminder>\nLine 1\nLine 2\n</system-reminder>\nEnd"
        result = filter_system_reminders(text)
        assert "<system-reminder>" not in result
        assert "Start" in result
        assert "End" in result

    def test_filter_no_reminders(self):
        """Test text without reminders is unchanged."""
        text = "No reminders here"
        result = filter_system_reminders(text)
        assert result == "No reminders here"

    def test_filter_empty_string(self):
        """Test filtering empty string."""
        result = filter_system_reminders("")
        assert result == ""

    def test_filter_only_reminder(self):
        """Test text that is only a reminder."""
        text = "<system-reminder>Only reminder content</system-reminder>"
        result = filter_system_reminders(text)
        assert result == ""


# =============================================================================
# clean_trajectory() Tests
# =============================================================================


@pytest.mark.unit
class TestCleanTrajectory:
    """Tests for clean_trajectory function."""

    def test_clean_removes_system_reminders(self):
        """Test that system reminders are removed from content."""
        trajectory = {
            "trace_id": "test",
            "messages": [
                {"role": "user", "content": "Hi <system-reminder>ignore</system-reminder> there"}
            ]
        }
        cleaned = clean_trajectory(trajectory)
        assert "<system-reminder>" not in cleaned["messages"][0]["content"]
        assert "Hi" in cleaned["messages"][0]["content"]

    def test_clean_removes_empty_messages(self):
        """Test that empty messages are removed."""
        trajectory = {
            "trace_id": "test",
            "messages": [
                {"role": "user", "content": "Valid"},
                {"role": "assistant", "content": ""},
                {"role": "assistant", "content": None},
                {"role": "user", "content": "Also valid"}
            ]
        }
        cleaned = clean_trajectory(trajectory)
        assert len(cleaned["messages"]) == 2

    def test_clean_preserves_tool_calls(self):
        """Test that messages with tool_calls are preserved."""
        trajectory = {
            "trace_id": "test",
            "messages": [
                {"role": "assistant", "tool_calls": [{"id": "1"}]}
            ]
        }
        cleaned = clean_trajectory(trajectory)
        assert len(cleaned["messages"]) == 1

    def test_clean_with_remove_reminders_false(self):
        """Test that reminders are preserved when flag is False."""
        trajectory = {
            "trace_id": "test",
            "messages": [
                {"role": "user", "content": "<system-reminder>keep me</system-reminder>"}
            ]
        }
        cleaned = clean_trajectory(trajectory, remove_system_reminders=False)
        assert "<system-reminder>" in cleaned["messages"][0]["content"]

    def test_clean_removes_only_reminder_messages(self):
        """Test that messages containing only reminders are removed."""
        trajectory = {
            "trace_id": "test",
            "messages": [
                {"role": "user", "content": "Keep"},
                {"role": "assistant", "content": "<system-reminder>only reminder</system-reminder>"}
            ]
        }
        cleaned = clean_trajectory(trajectory)
        assert len(cleaned["messages"]) == 1
        assert cleaned["messages"][0]["content"] == "Keep"

    def test_clean_preserves_metadata(self):
        """Test that trajectory metadata is preserved."""
        trajectory = {
            "trace_id": "t1",
            "span_id": "s1",
            "model": "claude-3",
            "messages": [{"role": "user", "content": "Hi"}]
        }
        cleaned = clean_trajectory(trajectory)
        assert cleaned["trace_id"] == "t1"
        assert cleaned["span_id"] == "s1"
        assert cleaned["model"] == "claude-3"


# =============================================================================
# format_trajectory_as_text() Tests
# =============================================================================


@pytest.mark.unit
class TestFormatTrajectoryAsText:
    """Tests for format_trajectory_as_text function."""

    def test_format_user_message(self):
        """Test formatting user message."""
        trajectory = {
            "trace_id": "abc123def456",
            "model": "claude-3",
            "timestamp": "2024-01-15T10:00:00Z",
            "messages": [
                {"role": "user", "content": "What is 2+2?"}
            ]
        }
        result = format_trajectory_as_text(trajectory)
        assert "[USER]" in result
        assert "What is 2+2?" in result
        assert "abc123def456"[:12] in result

    def test_format_assistant_message(self):
        """Test formatting assistant message."""
        trajectory = {
            "trace_id": "abc",
            "model": "claude-3",
            "timestamp": "2024-01-15",
            "messages": [
                {"role": "assistant", "content": "The answer is 4."}
            ]
        }
        result = format_trajectory_as_text(trajectory)
        assert "[ASSISTANT]" in result
        assert "The answer is 4." in result

    def test_format_with_thinking(self):
        """Test formatting assistant message with thinking."""
        trajectory = {
            "trace_id": "abc",
            "model": "claude-3",
            "timestamp": "2024-01-15",
            "messages": [
                {
                    "role": "assistant",
                    "thinking": "Let me calculate...",
                    "content": "The answer is 4."
                }
            ]
        }
        result = format_trajectory_as_text(trajectory, include_thinking=True)
        assert "<thinking>" in result
        assert "Let me calculate..." in result
        assert "</thinking>" in result

    def test_format_without_thinking(self):
        """Test formatting with thinking disabled."""
        trajectory = {
            "trace_id": "abc",
            "model": "claude-3",
            "timestamp": "2024-01-15",
            "messages": [
                {
                    "role": "assistant",
                    "thinking": "Hidden thought",
                    "content": "Visible content"
                }
            ]
        }
        result = format_trajectory_as_text(trajectory, include_thinking=False)
        assert "Hidden thought" not in result
        assert "Visible content" in result

    def test_format_tool_calls(self):
        """Test formatting tool calls."""
        trajectory = {
            "trace_id": "abc",
            "model": "claude-3",
            "timestamp": "2024-01-15",
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "tool_12345678901234567890",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "/test.txt"}'
                            }
                        }
                    ]
                }
            ]
        }
        result = format_trajectory_as_text(trajectory)
        assert "Tool call: read_file" in result
        assert "Arguments:" in result

    def test_format_tool_results(self):
        """Test formatting tool results."""
        trajectory = {
            "trace_id": "abc",
            "model": "claude-3",
            "timestamp": "2024-01-15",
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "tool_abc", "function": {"name": "read", "arguments": "{}"}}
                    ]
                },
                {
                    "role": "tool",
                    "tool_call_id": "tool_abc",
                    "content": "File contents here"
                }
            ]
        }
        result = format_trajectory_as_text(trajectory)
        assert "[TOOL RESULT]" in result
        assert "File contents here" in result

    def test_format_long_thinking_truncated(self):
        """Test that long thinking is truncated."""
        long_thinking = "A" * 600
        trajectory = {
            "trace_id": "abc",
            "model": "claude-3",
            "timestamp": "2024-01-15",
            "messages": [
                {"role": "assistant", "thinking": long_thinking, "content": "Answer"}
            ]
        }
        result = format_trajectory_as_text(trajectory)
        assert "..." in result
        assert len(result) < len(long_thinking) + 500

    def test_format_header_info(self):
        """Test that header info is included."""
        trajectory = {
            "trace_id": "trace_123456789",
            "model": "claude-3-opus",
            "timestamp": "2024-01-15T10:30:00Z",
            "messages": []
        }
        result = format_trajectory_as_text(trajectory)
        assert "Trajectory:" in result
        assert "Model: claude-3-opus" in result
        assert "Timestamp: 2024-01-15T10:30:00Z" in result


# =============================================================================
# extract_trajectory() Tests
# =============================================================================


@pytest.mark.unit
class TestExtractTrajectory:
    """Tests for extract_trajectory function."""

    def test_extract_basic_trajectory(self):
        """Test extracting a basic trajectory."""
        span = {
            "context": {"trace_id": "trace_1", "span_id": "span_1"},
            "start_time": "2024-01-15T10:00:00Z",
            "attributes": {
                "gen_ai.request.model": "claude-3",
                "gen_ai.prompt.0.role": "user",
                "gen_ai.prompt.0.content": "Hello",
                "gen_ai.completion.0.role": "assistant",
                "gen_ai.completion.0.content": "Hi there!"
            }
        }
        trajectory = extract_trajectory(span)

        assert trajectory["trace_id"] == "trace_1"
        assert trajectory["span_id"] == "span_1"
        assert trajectory["model"] == "claude-3"
        assert trajectory["timestamp"] == "2024-01-15T10:00:00Z"
        assert len(trajectory["messages"]) >= 2

    def test_extract_trajectory_with_usage(self):
        """Test extracting trajectory with usage info."""
        span = {
            "context": {"trace_id": "t1", "span_id": "s1"},
            "start_time": "2024-01-15",
            "attributes": {
                "gen_ai.request.model": "claude-3",
                "gen_ai.prompt.0.role": "user",
                "gen_ai.prompt.0.content": "Test",
                "gen_ai.usage.prompt_tokens": 100,
                "gen_ai.usage.completion_tokens": 50,
                "llm.usage.total_tokens": 150
            }
        }
        trajectory = extract_trajectory(span)

        assert trajectory["usage"]["prompt_tokens"] == 100
        assert trajectory["usage"]["completion_tokens"] == 50
        assert trajectory["usage"]["total_tokens"] == 150

    def test_extract_trajectory_unknown_model(self):
        """Test extracting trajectory without model info."""
        span = {
            "context": {"trace_id": "t1", "span_id": "s1"},
            "start_time": "2024-01-15",
            "attributes": {
                "gen_ai.prompt.0.role": "user",
                "gen_ai.prompt.0.content": "Test"
            }
        }
        trajectory = extract_trajectory(span)

        assert trajectory["model"] == "unknown"

    def test_extract_trajectory_expands_tool_results(self):
        """Test that tool results are expanded into individual messages."""
        tool_result_content = json.dumps([
            {"type": "tool_result", "tool_use_id": "t1", "content": "Result 1"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "Result 2"}
        ])
        span = {
            "context": {"trace_id": "t1", "span_id": "s1"},
            "start_time": "2024-01-15",
            "attributes": {
                "gen_ai.request.model": "claude-3",
                "gen_ai.prompt.0.role": "user",
                "gen_ai.prompt.0.content": tool_result_content
            }
        }
        trajectory = extract_trajectory(span)

        tool_messages = [m for m in trajectory["messages"] if m.get("role") == "tool"]
        assert len(tool_messages) == 2


# =============================================================================
# get_trajectories() Tests (with mocked network)
# =============================================================================


@pytest.mark.unit
class TestGetTrajectories:
    """Tests for get_trajectories function."""

    @patch("extract_trajectories.fetch_spans")
    def test_get_trajectories_filters_non_llm_spans(self, mock_fetch):
        """Test that non-LLM spans are filtered."""
        mock_fetch.return_value = [
            {"name": "other_span", "attributes": {}}
        ]

        result = get_trajectories()

        assert result == []

    @patch("extract_trajectories.fetch_spans")
    def test_get_trajectories_filters_error_spans(self, mock_fetch):
        """Test that error spans are filtered by default."""
        mock_fetch.return_value = [
            {
                "name": "litellm_request",
                "status_code": "ERROR",
                "context": {"trace_id": "t1", "span_id": "s1"},
                "attributes": {
                    "gen_ai.prompt.0.role": "user",
                    "gen_ai.prompt.0.content": "test"
                }
            }
        ]

        result = get_trajectories(include_errors=False)

        assert result == []

    @patch("extract_trajectories.fetch_spans")
    def test_get_trajectories_includes_errors_when_requested(self, mock_fetch):
        """Test that error spans are included when flag is set."""
        mock_fetch.return_value = [
            {
                "name": "litellm_request",
                "status_code": "ERROR",
                "context": {"trace_id": "t1", "span_id": "s1"},
                "start_time": "2024-01-15",
                "attributes": {
                    "gen_ai.request.model": "test",
                    "gen_ai.prompt.0.role": "user",
                    "gen_ai.prompt.0.content": "test message"
                }
            }
        ]

        result = get_trajectories(include_errors=True)

        assert len(result) == 1

    @patch("extract_trajectories.fetch_spans")
    def test_get_trajectories_filters_empty_messages(self, mock_fetch):
        """Test that spans without messages are filtered."""
        mock_fetch.return_value = [
            {
                "name": "litellm_request",
                "context": {"trace_id": "t1", "span_id": "s1"},
                "attributes": {}  # No gen_ai.prompt.* attributes
            }
        ]

        result = get_trajectories()

        assert result == []

    @patch("extract_trajectories.fetch_spans")
    def test_get_trajectories_cleans_by_default(self, mock_fetch):
        """Test that trajectories are cleaned by default."""
        mock_fetch.return_value = [
            {
                "name": "litellm_request",
                "context": {"trace_id": "t1", "span_id": "s1"},
                "start_time": "2024-01-15",
                "attributes": {
                    "gen_ai.request.model": "test",
                    "gen_ai.prompt.0.role": "user",
                    "gen_ai.prompt.0.content": "Hi <system-reminder>remove</system-reminder>"
                }
            }
        ]

        result = get_trajectories(clean=True)

        assert len(result) == 1
        assert "<system-reminder>" not in result[0]["messages"][0]["content"]

    @patch("extract_trajectories.fetch_spans")
    def test_get_trajectories_no_clean(self, mock_fetch):
        """Test trajectories without cleaning."""
        mock_fetch.return_value = [
            {
                "name": "litellm_request",
                "context": {"trace_id": "t1", "span_id": "s1"},
                "start_time": "2024-01-15",
                "attributes": {
                    "gen_ai.request.model": "test",
                    "gen_ai.prompt.0.role": "user",
                    "gen_ai.prompt.0.content": "<system-reminder>keep</system-reminder>"
                }
            }
        ]

        result = get_trajectories(clean=False)

        assert len(result) == 1
        assert "<system-reminder>" in result[0]["messages"][0]["content"]
