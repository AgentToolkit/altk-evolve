"""Fact hooks see supplied user text, never the extraction template."""

import datetime
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from altk_evolve.llm.fact_extraction import fact_extraction as facts


@pytest.mark.unit
@pytest.mark.parametrize("use_categorization", [False, True])
@pytest.mark.parametrize("redact", [False, True])
def test_hooks_only_receive_user_content(monkeypatch, use_categorization, redact):
    class FixedDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 16, 12, tzinfo=tz)

    monkeypatch.setattr(facts.datetime, "datetime", FixedDatetime)
    monkeypatch.setattr(facts.llm_settings, "categorization_mode", "predefined")
    messages = [
        {"role": "system", "content": "System instructions stay out of extraction."},
        {"role": "user", "content": "I am John and live in San Francisco.", "id": "keep-this-id"},
        {"role": "assistant", "content": "Assistant text stays out of extraction."},
        {"role": "USER", "content": "I prefer short paragraphs."},
    ]
    original = deepcopy(messages)
    user_messages = [
        {"role": "user", "content": messages[1]["content"] + "\n" + messages[3]["content"]},
    ]
    expected_messages = deepcopy(user_messages)
    if redact:
        expected_messages[0]["content"] = "I am [REDACTED] and live in [REDACTED].\nI prefer short paragraphs."

    def hook(payload, **kwargs):
        assert payload == user_messages
        payload = deepcopy(payload)
        # These names also appear in the template. Only user occurrences may change.
        if redact:
            for message in payload:
                message["content"] = message["content"].replace("John", "[REDACTED]").replace("San Francisco", "[REDACTED]")
        return payload

    dispatch = Mock(side_effect=hook)
    monkeypatch.setattr(facts, "dispatch_llm_pre_call", dispatch)
    completion = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"facts": []}'))]))
    monkeypatch.setattr(facts, "completion", completion)

    assert facts.extract_facts_from_messages(messages, use_categorization) == []

    dispatch.assert_called_once()
    assert dispatch.call_args.kwargs == {"purpose": "fact_extraction", "model": facts.llm_settings.fact_extraction_model}
    prompt = completion.call_args.kwargs["messages"][0]["content"]
    # Compare the complete prompt, including all instructions and examples.
    assert prompt == facts._build_prompt(expected_messages, use_categorization)
    assert "Input: Hi, I am looking for a restaurant in San Francisco." in prompt
    assert messages == original


@pytest.mark.unit
@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        [None],
        [{"role": "assistant", "content": "text"}],
        [{"role": "user", "content": None}],
        [{"role": "user", "content": []}],
        [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],
    ],
)
def test_malformed_hook_response_never_reaches_model(monkeypatch, result):
    monkeypatch.setattr(facts, "dispatch_llm_pre_call", Mock(return_value=result))
    completion = Mock()
    monkeypatch.setattr(facts, "completion", completion)
    with pytest.raises(ValueError, match="exactly one user message"):
        facts.extract_facts_from_messages([{"role": "user", "content": "private"}], False)
    completion.assert_not_called()


@pytest.mark.unit
def test_joined_content_is_redacted_once_and_reused_on_retry(monkeypatch):
    import re

    messages = [
        {"role": "user", "content": "my number is 415-555"},
        {"role": "user", "content": "2671 thanks"},
        {"role": "USER", "content": {"nested": ["original"]}},
    ]
    original = deepcopy(messages)

    def hook(payload, **kwargs):
        assert len(payload) == 1
        return [{"role": "user", "content": re.sub(r"415-555\s+2671", "[REDACTED]", payload[0]["content"])}]

    dispatch = Mock(side_effect=hook)
    monkeypatch.setattr(facts, "dispatch_llm_pre_call", dispatch)
    completion = Mock(
        side_effect=[RuntimeError("retry"), SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"facts": []}'))])]
    )
    monkeypatch.setattr(facts, "completion", completion)
    assert facts.extract_facts_from_messages(messages, False) == []
    dispatch.assert_called_once()
    assert completion.call_count == 2
    for call in completion.call_args_list:
        prompt = call.kwargs["messages"][0]["content"]
        assert "my number is [REDACTED] thanks" in prompt
        assert "415-555" not in prompt
    assert messages == original
