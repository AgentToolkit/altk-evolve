"""Tests for gist generation."""

import json
from unittest.mock import MagicMock, patch

import pytest

from kaizen.llm.gist.gist import _chunk_messages, _estimate_tokens, generate_gist
from kaizen.schema.gist import GistResult


@pytest.mark.unit
class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 0

    def test_short_string(self):
        assert _estimate_tokens("hello world") == 2  # 11 chars // 4

    def test_long_string(self):
        text = "a" * 1000
        assert _estimate_tokens(text) == 250


@pytest.mark.unit
class TestChunkMessages:
    def test_single_chunk_when_within_budget(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        chunks = _chunk_messages(messages, context_budget=64000)
        assert len(chunks) == 1
        assert chunks[0] == messages

    def test_splits_when_exceeds_budget(self):
        # Each message ~250 tokens (1000 chars), budget 3000 tokens
        # Available = 3000 - 2000 (reserved) = 1000 tokens, so ~4 messages per chunk
        messages = [{"role": "user", "content": "x" * 1000} for _ in range(10)]
        chunks = _chunk_messages(messages, context_budget=3000)
        assert len(chunks) > 1
        # All messages accounted for
        total = sum(len(chunk) for chunk in chunks)
        assert total == 10

    def test_empty_messages(self):
        chunks = _chunk_messages([], context_budget=64000)
        assert chunks == []

    def test_single_large_message_gets_own_chunk(self):
        # One huge message that exceeds budget on its own
        messages = [
            {"role": "user", "content": "x" * 300000},  # ~75k tokens
            {"role": "user", "content": "small"},
        ]
        chunks = _chunk_messages(messages, context_budget=64000)
        # The large message gets its own chunk, the small one gets another
        assert len(chunks) == 2


@pytest.mark.unit
class TestGenerateGist:
    @patch("kaizen.llm.gist.gist.get_supported_openai_params")
    @patch("kaizen.llm.gist.gist.supports_response_schema")
    @patch("kaizen.llm.gist.gist.completion")
    def test_generates_gist_from_messages(self, mock_completion, mock_supports, mock_params):
        mock_params.return_value = ["response_format"]
        mock_supports.return_value = True

        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps({"gist": "user prefers Python for data analysis"})
        mock_completion.return_value = mock_response

        messages = [
            {"role": "user", "content": "I really prefer Python over R for data work."},
            {"role": "assistant", "content": "Got it, Python it is."},
        ]
        result = generate_gist(messages, conversation_id="test-123")

        assert isinstance(result, GistResult)
        assert len(result.gists) == 1
        assert "Python" in result.gists[0]
        assert result.conversation_id == "test-123"
        assert result.message_count == 2
        assert result.chunk_count == 1

    @patch("kaizen.llm.gist.gist.get_supported_openai_params")
    @patch("kaizen.llm.gist.gist.supports_response_schema")
    @patch("kaizen.llm.gist.gist.completion")
    def test_returns_empty_on_no_user_signal(self, mock_completion, mock_supports, mock_params):
        mock_params.return_value = ["response_format"]
        mock_supports.return_value = True

        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps({"gist": "no user signal"})
        mock_completion.return_value = mock_response

        messages = [
            {"role": "user", "content": "What time is it?"},
            {"role": "assistant", "content": "It's 3pm."},
        ]
        result = generate_gist(messages)

        assert result.gists == []

    def test_empty_messages_returns_empty(self):
        result = generate_gist([])
        assert result.gists == []
        assert result.message_count == 0

    @patch("kaizen.llm.gist.gist.get_supported_openai_params")
    @patch("kaizen.llm.gist.gist.supports_response_schema")
    @patch("kaizen.llm.gist.gist.completion")
    def test_retries_on_parse_failure(self, mock_completion, mock_supports, mock_params):
        mock_params.return_value = ["response_format"]
        mock_supports.return_value = True

        # First two calls fail, third succeeds
        bad_response = MagicMock()
        bad_response.choices[0].message.content = "not json"

        good_response = MagicMock()
        good_response.choices[0].message.content = json.dumps({"gist": "user likes cats"})

        mock_completion.side_effect = [bad_response, bad_response, good_response]

        result = generate_gist([{"role": "user", "content": "I love cats"}])
        assert len(result.gists) == 1
        assert "cats" in result.gists[0]

    @patch("kaizen.llm.gist.gist.get_supported_openai_params")
    @patch("kaizen.llm.gist.gist.supports_response_schema")
    @patch("kaizen.llm.gist.gist.completion")
    def test_fallback_without_constrained_decoding(self, mock_completion, mock_supports, mock_params):
        mock_params.return_value = []  # No response_format support
        mock_supports.return_value = False

        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps({"gist": "user is a backend engineer"})
        mock_completion.return_value = mock_response

        result = generate_gist([{"role": "user", "content": "I work on backend systems"}])
        assert len(result.gists) == 1
        assert "backend" in result.gists[0]
