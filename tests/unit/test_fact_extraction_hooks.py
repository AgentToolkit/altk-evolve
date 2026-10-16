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
        {"role": "user", "content": messages[1]["content"]},
        {"role": "user", "content": messages[3]["content"]},
    ]
    expected_messages = deepcopy(user_messages)
    if redact:
        expected_messages[0]["content"] = "I am [REDACTED] and live in [REDACTED]."

    def hook(payload, **kwargs):
        assert payload == user_messages
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
