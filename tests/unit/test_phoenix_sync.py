"""Tests for Phoenix Sync functionality."""

import json
from unittest.mock import MagicMock, patch, Mock

import pytest

from kaizen.sync.phoenix_sync import PhoenixSync, SyncResult

# Mark all tests in this module as phoenix tests (skipped by default)
pytestmark = pytest.mark.phoenix


@pytest.fixture
def phoenix_sync():
    """Create a PhoenixSync instance with mocked client."""
    with patch("kaizen.sync.phoenix_sync.KaizenClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        sync = PhoenixSync(phoenix_url="http://test-phoenix:6006", namespace_id="test_namespace", project="test_project")
        sync.client = mock_client
        yield sync


# =============================================================================
# _parse_content() Tests
# =============================================================================


@pytest.mark.unit
class TestParseContent:
    """Tests for _parse_content method."""

    def test_parse_content_json_string(self, phoenix_sync):
        """Test parsing a JSON string."""
        content = '{"key": "value", "number": 42}'
        result = phoenix_sync._parse_content(content)
        assert result == {"key": "value", "number": 42}

    def test_parse_content_json_list(self, phoenix_sync):
        """Test parsing a JSON list string."""
        content = '[{"type": "text", "text": "hello"}]'
        result = phoenix_sync._parse_content(content)
        assert result == [{"type": "text", "text": "hello"}]

    def test_parse_content_python_literal(self, phoenix_sync):
        """Test parsing a Python literal string."""
        content = "{'key': 'value'}"  # Single quotes - not valid JSON
        result = phoenix_sync._parse_content(content)
        assert result == {"key": "value"}

    def test_parse_content_plain_string(self, phoenix_sync):
        """Test that plain strings are returned as-is."""
        content = "This is just plain text"
        result = phoenix_sync._parse_content(content)
        assert result == "This is just plain text"

    def test_parse_content_passthrough_dict(self, phoenix_sync):
        """Test that dicts are passed through unchanged."""
        content = {"already": "parsed"}
        result = phoenix_sync._parse_content(content)
        assert result == {"already": "parsed"}

    def test_parse_content_passthrough_list(self, phoenix_sync):
        """Test that lists are passed through unchanged."""
        content = [{"type": "text"}]
        result = phoenix_sync._parse_content(content)
        assert result == [{"type": "text"}]

    def test_parse_content_invalid_json_returns_string(self, phoenix_sync):
        """Test that invalid JSON/Python returns the original string."""
        content = "not valid {json or python"
        result = phoenix_sync._parse_content(content)
        assert result == content


# =============================================================================
# _extract_messages_from_span() Tests
# =============================================================================


@pytest.mark.unit
class TestExtractMessagesFromSpan:
    """Tests for _extract_messages_from_span method."""

    def test_extract_single_prompt(self, phoenix_sync):
        """Test extracting a single prompt message."""
        span = {"attributes": {"gen_ai.prompt.0.role": "user", "gen_ai.prompt.0.content": "Hello, world!"}}
        messages = phoenix_sync._extract_messages_from_span(span)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello, world!"
        assert messages[0]["type"] == "prompt"
        assert messages[0]["index"] == 0

    def test_extract_multiple_prompts(self, phoenix_sync):
        """Test extracting multiple prompt messages."""
        span = {
            "attributes": {
                "gen_ai.prompt.0.role": "system",
                "gen_ai.prompt.0.content": "You are a helpful assistant.",
                "gen_ai.prompt.1.role": "user",
                "gen_ai.prompt.1.content": "What is 2+2?",
            }
        }
        messages = phoenix_sync._extract_messages_from_span(span)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_extract_with_completion(self, phoenix_sync):
        """Test extracting prompts and completions."""
        span = {
            "attributes": {
                "gen_ai.prompt.0.role": "user",
                "gen_ai.prompt.0.content": "Hi",
                "gen_ai.completion.0.role": "assistant",
                "gen_ai.completion.0.content": "Hello! How can I help?",
            }
        }
        messages = phoenix_sync._extract_messages_from_span(span)
        assert len(messages) == 2
        prompts = [m for m in messages if m["type"] == "prompt"]
        completions = [m for m in messages if m["type"] == "completion"]
        assert len(prompts) == 1
        assert len(completions) == 1

    def test_extract_empty_span(self, phoenix_sync):
        """Test extracting from span with no messages."""
        span = {"attributes": {}}
        messages = phoenix_sync._extract_messages_from_span(span)
        assert messages == []

    def test_extract_parses_json_content(self, phoenix_sync):
        """Test that JSON content in attributes is parsed."""
        span = {"attributes": {"gen_ai.prompt.0.role": "assistant", "gen_ai.prompt.0.content": '[{"type": "text", "text": "Hello"}]'}}
        messages = phoenix_sync._extract_messages_from_span(span)
        assert len(messages) == 1
        assert messages[0]["content"] == [{"type": "text", "text": "Hello"}]

    def test_extract_handles_non_sequential_indices(self, phoenix_sync):
        """Test handling non-sequential message indices."""
        span = {
            "attributes": {
                "gen_ai.prompt.0.role": "user",
                "gen_ai.prompt.0.content": "First",
                "gen_ai.prompt.5.role": "user",
                "gen_ai.prompt.5.content": "Second",
            }
        }
        messages = phoenix_sync._extract_messages_from_span(span)
        assert len(messages) == 2
        # Should be sorted by index
        assert messages[0]["index"] == 0
        assert messages[1]["index"] == 5


# =============================================================================
# _convert_to_openai_format() Tests
# =============================================================================


@pytest.mark.unit
class TestConvertToOpenAIFormat:
    """Tests for _convert_to_openai_format method."""

    def test_convert_simple_string(self, phoenix_sync):
        """Test converting a simple string message."""
        result = phoenix_sync._convert_to_openai_format("Hello", "user")
        assert result == {"role": "user", "content": "Hello"}

    def test_convert_text_block(self, phoenix_sync):
        """Test converting Anthropic text block."""
        content = [{"type": "text", "text": "Hello, world!"}]
        result = phoenix_sync._convert_to_openai_format(content, "assistant")
        assert result["role"] == "assistant"
        assert result["content"] == "Hello, world!"

    def test_convert_multiple_text_blocks(self, phoenix_sync):
        """Test converting multiple text blocks."""
        content = [{"type": "text", "text": "First part"}, {"type": "text", "text": "Second part"}]
        result = phoenix_sync._convert_to_openai_format(content, "assistant")
        assert result["content"] == "First part\n\nSecond part"

    def test_convert_thinking_block(self, phoenix_sync):
        """Test converting Anthropic thinking block."""
        content = [{"type": "thinking", "thinking": "Let me analyze this..."}, {"type": "text", "text": "The answer is 42."}]
        result = phoenix_sync._convert_to_openai_format(content, "assistant")
        assert result["role"] == "assistant"
        assert result["thinking"] == "Let me analyze this..."
        assert result["content"] == "The answer is 42."

    def test_convert_tool_use_block(self, phoenix_sync):
        """Test converting Anthropic tool_use block."""
        content = [{"type": "tool_use", "id": "tool_123", "name": "read_file", "input": {"path": "/tmp/test.txt"}}]
        result = phoenix_sync._convert_to_openai_format(content, "assistant")
        assert result["role"] == "assistant"
        assert "tool_calls" in result
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["id"] == "tool_123"
        assert result["tool_calls"][0]["type"] == "function"
        assert result["tool_calls"][0]["function"]["name"] == "read_file"
        assert json.loads(result["tool_calls"][0]["function"]["arguments"]) == {"path": "/tmp/test.txt"}

    def test_convert_tool_result_block(self, phoenix_sync):
        """Test converting Anthropic tool_result block."""
        content = [{"type": "tool_result", "tool_use_id": "tool_123", "content": "File contents here", "is_error": False}]
        result = phoenix_sync._convert_to_openai_format(content, "user")
        assert result["role"] == "tool"
        assert "tool_results" in result
        assert result["tool_results"][0]["tool_call_id"] == "tool_123"
        assert result["tool_results"][0]["content"] == "File contents here"

    def test_convert_mixed_content_blocks(self, phoenix_sync):
        """Test converting mixed content blocks."""
        content = [
            {"type": "thinking", "thinking": "I need to read the file first"},
            {"type": "text", "text": "Let me check that file."},
            {"type": "tool_use", "id": "tool_456", "name": "read_file", "input": {"path": "/etc/hosts"}},
        ]
        result = phoenix_sync._convert_to_openai_format(content, "assistant")
        assert result["role"] == "assistant"
        assert result["thinking"] == "I need to read the file first"
        assert result["content"] == "Let me check that file."
        assert len(result["tool_calls"]) == 1

    def test_convert_filters_no_content_text(self, phoenix_sync):
        """Test that '(no content)' text is filtered out."""
        content = [{"type": "text", "text": "(no content)"}, {"type": "text", "text": "Real content"}]
        result = phoenix_sync._convert_to_openai_format(content, "assistant")
        assert result["content"] == "Real content"

    def test_convert_assistant_only_tool_calls(self, phoenix_sync):
        """Test assistant message with only tool calls (no text)."""
        content = [{"type": "tool_use", "id": "tool_789", "name": "bash", "input": {"command": "ls"}}]
        result = phoenix_sync._convert_to_openai_format(content, "assistant")
        assert result["role"] == "assistant"
        assert result.get("content") is None
        assert len(result["tool_calls"]) == 1

    def test_convert_non_dict_in_list(self, phoenix_sync):
        """Test handling non-dict items in content list."""
        content = ["plain string", {"type": "text", "text": "dict item"}]
        result = phoenix_sync._convert_to_openai_format(content, "user")
        assert "plain string" in result["content"]

    def test_convert_non_list_non_string(self, phoenix_sync):
        """Test handling content that is neither list nor string."""
        result = phoenix_sync._convert_to_openai_format(12345, "user")
        assert result == {"role": "user", "content": "12345"}


# =============================================================================
# _extract_trajectory() Tests
# =============================================================================


@pytest.mark.unit
class TestExtractTrajectory:
    """Tests for _extract_trajectory method."""

    def test_extract_full_trajectory(self, phoenix_sync):
        """Test extracting a complete trajectory."""
        span = {
            "context": {"trace_id": "trace_abc123", "span_id": "span_xyz789"},
            "start_time": "2024-01-15T10:30:00Z",
            "attributes": {
                "gen_ai.request.model": "claude-3-opus",
                "gen_ai.prompt.0.role": "user",
                "gen_ai.prompt.0.content": "What is 2+2?",
                "gen_ai.completion.0.role": "assistant",
                "gen_ai.completion.0.content": "2+2 equals 4.",
                "gen_ai.usage.prompt_tokens": 10,
                "gen_ai.usage.completion_tokens": 8,
                "llm.usage.total_tokens": 18,
            },
        }
        trajectory = phoenix_sync._extract_trajectory(span)

        assert trajectory["trace_id"] == "trace_abc123"
        assert trajectory["span_id"] == "span_xyz789"
        assert trajectory["model"] == "claude-3-opus"
        assert trajectory["timestamp"] == "2024-01-15T10:30:00Z"
        assert len(trajectory["messages"]) == 2
        assert trajectory["usage"]["prompt_tokens"] == 10
        assert trajectory["usage"]["completion_tokens"] == 8
        assert trajectory["usage"]["total_tokens"] == 18

    def test_extract_trajectory_with_tool_calls(self, phoenix_sync):
        """Test extracting trajectory with tool calls."""
        tool_use_content = json.dumps(
            [
                {"type": "text", "text": "I'll read that file."},
                {"type": "tool_use", "id": "tool_1", "name": "read_file", "input": {"path": "/test"}},
            ]
        )
        tool_result_content = json.dumps([{"type": "tool_result", "tool_use_id": "tool_1", "content": "file contents"}])

        span = {
            "context": {"trace_id": "trace_1", "span_id": "span_1"},
            "start_time": "2024-01-15T10:30:00Z",
            "attributes": {
                "gen_ai.request.model": "claude-3",
                "gen_ai.prompt.0.role": "user",
                "gen_ai.prompt.0.content": "Read /test",
                "gen_ai.prompt.1.role": "assistant",
                "gen_ai.prompt.1.content": tool_use_content,
                "gen_ai.prompt.2.role": "user",
                "gen_ai.prompt.2.content": tool_result_content,
                "gen_ai.completion.0.role": "assistant",
                "gen_ai.completion.0.content": "The file contains: file contents",
            },
        }
        trajectory = phoenix_sync._extract_trajectory(span)

        # Should have: user, assistant with tool_call, tool result, assistant response
        messages = trajectory["messages"]
        assert any(m.get("tool_calls") for m in messages)
        assert any(m.get("role") == "tool" for m in messages)


# =============================================================================
# _clean_trajectory() Tests
# =============================================================================


@pytest.mark.unit
class TestCleanTrajectory:
    """Tests for _clean_trajectory method."""

    def test_clean_removes_system_reminders(self, phoenix_sync):
        """Test that system reminders are removed."""
        trajectory = {
            "trace_id": "test",
            "messages": [{"role": "user", "content": "Hello <system-reminder>This is a reminder</system-reminder> there"}],
        }
        cleaned = phoenix_sync._clean_trajectory(trajectory)
        assert "<system-reminder>" not in cleaned["messages"][0]["content"]
        assert "Hello" in cleaned["messages"][0]["content"]
        assert "there" in cleaned["messages"][0]["content"]

    def test_clean_removes_multiline_system_reminders(self, phoenix_sync):
        """Test that multiline system reminders are removed."""
        trajectory = {
            "trace_id": "test",
            "messages": [{"role": "assistant", "content": "Start\n<system-reminder>\nLine 1\nLine 2\n</system-reminder>\nEnd"}],
        }
        cleaned = phoenix_sync._clean_trajectory(trajectory)
        assert "<system-reminder>" not in cleaned["messages"][0]["content"]
        assert "Start" in cleaned["messages"][0]["content"]
        assert "End" in cleaned["messages"][0]["content"]

    def test_clean_removes_empty_messages(self, phoenix_sync):
        """Test that empty messages are removed."""
        trajectory = {
            "trace_id": "test",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": ""},
                {"role": "assistant", "content": None},
                {"role": "user", "content": "World"},
            ],
        }
        cleaned = phoenix_sync._clean_trajectory(trajectory)
        assert len(cleaned["messages"]) == 2
        assert cleaned["messages"][0]["content"] == "Hello"
        assert cleaned["messages"][1]["content"] == "World"

    def test_clean_preserves_tool_calls(self, phoenix_sync):
        """Test that messages with tool_calls but no content are preserved."""
        trajectory = {"trace_id": "test", "messages": [{"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "test"}}]}]}
        cleaned = phoenix_sync._clean_trajectory(trajectory)
        assert len(cleaned["messages"]) == 1
        assert "tool_calls" in cleaned["messages"][0]

    def test_clean_removes_only_reminder_content(self, phoenix_sync):
        """Test that messages with only system reminders are removed."""
        trajectory = {
            "trace_id": "test",
            "messages": [
                {"role": "user", "content": "Valid"},
                {"role": "assistant", "content": "<system-reminder>Only reminder</system-reminder>"},
                {"role": "user", "content": "Also valid"},
            ],
        }
        cleaned = phoenix_sync._clean_trajectory(trajectory)
        assert len(cleaned["messages"]) == 2

    def test_clean_preserves_non_string_content(self, phoenix_sync):
        """Test that non-string content is preserved."""
        trajectory = {"trace_id": "test", "messages": [{"role": "user", "content": ["list", "content"]}]}
        cleaned = phoenix_sync._clean_trajectory(trajectory)
        assert len(cleaned["messages"]) == 1
        assert cleaned["messages"][0]["content"] == ["list", "content"]


# =============================================================================
# sync() Tests
# =============================================================================


@pytest.mark.unit
class TestSync:
    """Tests for sync method."""

    @patch("kaizen.sync.phoenix_sync.urllib.request.urlopen")
    @patch("kaizen.sync.phoenix_sync.generate_tips")
    def test_sync_creates_namespace_if_not_exists(self, mock_generate_tips, mock_urlopen, phoenix_sync):
        """Test that sync creates namespace if it doesn't exist."""
        from kaizen.schema.exceptions import NamespaceNotFoundException

        phoenix_sync.client.get_namespace_details.side_effect = NamespaceNotFoundException()
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"data": [], "next_cursor": null}'
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        phoenix_sync.sync(limit=10)

        phoenix_sync.client.create_namespace.assert_called_once_with("test_namespace")

    @patch("kaizen.sync.phoenix_sync.urllib.request.urlopen")
    @patch("kaizen.sync.phoenix_sync.generate_tips")
    def test_sync_skips_already_processed(self, mock_generate_tips, mock_urlopen, phoenix_sync):
        """Test that already processed spans are skipped."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "data": [
                    {
                        "name": "litellm_request",
                        "context": {"trace_id": "t1", "span_id": "already_processed"},
                        "attributes": {"gen_ai.prompt.0.role": "user", "gen_ai.prompt.0.content": "test"},
                    }
                ],
                "next_cursor": None,
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        # Mock that this span was already processed
        mock_entity = MagicMock()
        mock_entity.metadata = {"span_id": "already_processed"}
        phoenix_sync.client.search_entities.return_value = [mock_entity]

        result = phoenix_sync.sync(limit=10)

        assert result.skipped == 1
        assert result.processed == 0

    @patch("kaizen.sync.phoenix_sync.urllib.request.urlopen")
    @patch("kaizen.sync.phoenix_sync.generate_tips")
    def test_sync_filters_error_spans(self, mock_generate_tips, mock_urlopen, phoenix_sync):
        """Test that error spans are filtered by default."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "data": [
                    {
                        "name": "litellm_request",
                        "status_code": "ERROR",
                        "context": {"trace_id": "t1", "span_id": "s1"},
                        "attributes": {"gen_ai.prompt.0.role": "user", "gen_ai.prompt.0.content": "test"},
                    }
                ],
                "next_cursor": None,
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        phoenix_sync.client.search_entities.return_value = []

        result = phoenix_sync.sync(limit=10, include_errors=False)

        assert result.processed == 0

    @patch("kaizen.sync.phoenix_sync.urllib.request.urlopen")
    @patch("kaizen.sync.phoenix_sync.generate_tips")
    def test_sync_includes_error_spans_when_requested(self, mock_generate_tips, mock_urlopen, phoenix_sync):
        """Test that error spans are included when include_errors=True."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "data": [
                    {
                        "name": "litellm_request",
                        "status_code": "ERROR",
                        "context": {"trace_id": "t1", "span_id": "s1"},
                        "start_time": "2024-01-15T10:00:00Z",
                        "attributes": {
                            "gen_ai.request.model": "test-model",
                            "gen_ai.prompt.0.role": "user",
                            "gen_ai.prompt.0.content": "test message",
                        },
                    }
                ],
                "next_cursor": None,
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        phoenix_sync.client.search_entities.return_value = []
        mock_generate_tips.return_value = []

        result = phoenix_sync.sync(limit=10, include_errors=True)

        assert result.processed == 1

    @patch("kaizen.sync.phoenix_sync.urllib.request.urlopen")
    @patch("kaizen.sync.phoenix_sync.generate_tips")
    def test_sync_filters_non_llm_spans(self, mock_generate_tips, mock_urlopen, phoenix_sync):
        """Test that non-LLM spans are filtered out."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"data": [{"name": "some_other_span", "context": {"trace_id": "t1", "span_id": "s1"}, "attributes": {}}], "next_cursor": None}
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        phoenix_sync.client.search_entities.return_value = []

        result = phoenix_sync.sync(limit=10)

        assert result.processed == 0

    @patch("kaizen.sync.phoenix_sync.urllib.request.urlopen")
    @patch("kaizen.sync.phoenix_sync.generate_tips")
    def test_sync_processes_valid_spans(self, mock_generate_tips, mock_urlopen, phoenix_sync):
        """Test that valid spans are processed."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "data": [
                    {
                        "name": "litellm_request",
                        "context": {"trace_id": "t1", "span_id": "s1"},
                        "start_time": "2024-01-15T10:00:00Z",
                        "attributes": {
                            "gen_ai.request.model": "claude-3",
                            "gen_ai.prompt.0.role": "user",
                            "gen_ai.prompt.0.content": "Hello",
                        },
                    }
                ],
                "next_cursor": None,
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        phoenix_sync.client.search_entities.return_value = []
        # Create mock Tip objects with required attributes
        mock_tip1 = MagicMock()
        mock_tip1.content = "Tip 1 content"
        mock_tip1.category = "strategy"
        mock_tip1.rationale = "Tip 1 rationale"
        mock_tip1.trigger = "Tip 1 trigger"
        mock_tip2 = MagicMock()
        mock_tip2.content = "Tip 2 content"
        mock_tip2.category = "optimization"
        mock_tip2.rationale = "Tip 2 rationale"
        mock_tip2.trigger = "Tip 2 trigger"
        mock_generate_tips.return_value = [mock_tip1, mock_tip2]

        result = phoenix_sync.sync(limit=10)

        assert result.processed == 1
        assert result.tips_generated == 2
        phoenix_sync.client.update_entities.assert_called()

    @patch("kaizen.sync.phoenix_sync.urllib.request.urlopen")
    @patch("kaizen.sync.phoenix_sync.generate_tips")
    def test_sync_returns_correct_counts(self, mock_generate_tips, mock_urlopen, phoenix_sync):
        """Test that sync returns correct counts in SyncResult."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "data": [
                    {
                        "name": "litellm_request",
                        "context": {"trace_id": "t1", "span_id": "new_span"},
                        "start_time": "2024-01-15T10:00:00Z",
                        "attributes": {
                            "gen_ai.request.model": "claude-3",
                            "gen_ai.prompt.0.role": "user",
                            "gen_ai.prompt.0.content": "New message",
                        },
                    },
                    {
                        "name": "litellm_request",
                        "context": {"trace_id": "t2", "span_id": "old_span"},
                        "start_time": "2024-01-15T09:00:00Z",
                        "attributes": {
                            "gen_ai.request.model": "claude-3",
                            "gen_ai.prompt.0.role": "user",
                            "gen_ai.prompt.0.content": "Old message",
                        },
                    },
                ],
                "next_cursor": None,
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        # old_span was already processed
        mock_entity = MagicMock()
        mock_entity.metadata = {"span_id": "old_span"}
        phoenix_sync.client.search_entities.return_value = [mock_entity]
        # Create mock Tip object with required attributes
        mock_tip = MagicMock()
        mock_tip.content = "Generated tip content"
        mock_tip.category = "strategy"
        mock_tip.rationale = "Tip rationale"
        mock_tip.trigger = "Tip trigger"
        mock_generate_tips.return_value = [mock_tip]

        result = phoenix_sync.sync(limit=10)

        assert isinstance(result, SyncResult)
        assert result.processed == 1
        assert result.skipped == 1
        assert result.tips_generated == 1
        assert result.errors == []

    @patch("kaizen.sync.phoenix_sync.urllib.request.urlopen")
    @patch("kaizen.sync.phoenix_sync.generate_tips")
    def test_sync_handles_processing_errors(self, mock_generate_tips, mock_urlopen, phoenix_sync):
        """Test that processing errors are captured."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "data": [
                    {
                        "name": "litellm_request",
                        "context": {"trace_id": "t1", "span_id": "s1"},
                        "start_time": "2024-01-15T10:00:00Z",
                        "attributes": {
                            "gen_ai.request.model": "claude-3",
                            "gen_ai.prompt.0.role": "user",
                            "gen_ai.prompt.0.content": "test",
                        },
                    }
                ],
                "next_cursor": None,
            }
        ).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        phoenix_sync.client.search_entities.return_value = []
        mock_generate_tips.side_effect = Exception("Tip generation failed")

        result = phoenix_sync.sync(limit=10)

        assert result.processed == 0
        assert len(result.errors) == 1
        assert "Tip generation failed" in result.errors[0]


# =============================================================================
# _ensure_namespace() Tests
# =============================================================================


@pytest.mark.unit
class TestEnsureNamespace:
    """Tests for _ensure_namespace method."""

    def test_ensure_namespace_exists(self, phoenix_sync):
        """Test that existing namespace is not recreated."""
        phoenix_sync.client.get_namespace_details.return_value = MagicMock()

        phoenix_sync._ensure_namespace()

        phoenix_sync.client.create_namespace.assert_not_called()

    def test_ensure_namespace_creates_if_missing(self, phoenix_sync):
        """Test that missing namespace is created."""
        from kaizen.schema.exceptions import NamespaceNotFoundException

        phoenix_sync.client.get_namespace_details.side_effect = NamespaceNotFoundException()

        phoenix_sync._ensure_namespace()

        phoenix_sync.client.create_namespace.assert_called_once_with("test_namespace")


# =============================================================================
# _get_processed_span_ids() Tests
# =============================================================================


@pytest.mark.unit
class TestGetProcessedSpanIds:
    """Tests for _get_processed_span_ids method."""

    def test_get_processed_span_ids_empty(self, phoenix_sync):
        """Test getting processed IDs when none exist."""
        phoenix_sync.client.search_entities.return_value = []

        result = phoenix_sync._get_processed_span_ids()

        assert result == set()

    def test_get_processed_span_ids_with_entities(self, phoenix_sync):
        """Test getting processed IDs from existing entities."""
        entity1 = MagicMock()
        entity1.metadata = {"span_id": "span_1"}
        entity2 = MagicMock()
        entity2.metadata = {"span_id": "span_2"}
        entity3 = MagicMock()
        entity3.metadata = None  # No metadata

        phoenix_sync.client.search_entities.return_value = [entity1, entity2, entity3]

        result = phoenix_sync._get_processed_span_ids()

        assert result == {"span_1", "span_2"}

    def test_get_processed_span_ids_namespace_not_found(self, phoenix_sync):
        """Test that missing namespace returns empty set."""
        from kaizen.schema.exceptions import NamespaceNotFoundException

        phoenix_sync.client.search_entities.side_effect = NamespaceNotFoundException()

        result = phoenix_sync._get_processed_span_ids()

        assert result == set()
