"""Conflict routing must survive redactors that mask numbers in free text."""

import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from altk_evolve.llm.conflict_resolution import conflict_resolution as cr
from altk_evolve.schema.core import RecordedEntity

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("event", ["ADD", "UPDATE", "DELETE", "NONE"])
@pytest.mark.parametrize("retry", [False, True])
def test_conflict_prompt_keeps_ids_but_redacts_content(monkeypatch, event, retry):
    old = RecordedEntity(id="6", type="guideline", content="Email alice@example.com with 6 reports", created_at=datetime.now())
    new = RecordedEntity(id="7", type="guideline", content="Email bob@example.com with 8 reports", created_at=datetime.now())

    def redact(messages, **kwargs):
        return [{**m, "content": re.sub(r"\b\d+\b|\w+@example\.com", "[REDACTED]", m["content"])} for m in messages]

    attempts = []

    def complete(**kwargs):
        attempts.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        current = prompt.split("Below is a list of entities which I have collected up until now:", 1)[1]
        existing = json.JSONDecoder().raw_decode(current.lstrip())[0]
        incoming = json.loads(current.split("```", 2)[1])
        assert existing[0]["id"] == "6"
        assert incoming[0]["id"] == "7"
        assert existing[0]["type"] == incoming[0]["type"] == "guideline"
        assert "alice@example.com" not in prompt and "bob@example.com" not in prompt and "admin@example.com" not in prompt
        assert "with 6 reports" not in prompt and "with 8 reports" not in prompt
        target = incoming[0] if event == "ADD" else existing[0]
        decisions = [{**target, "event": event, "incoming_ids": ["7"]}]
        if retry and len(attempts) == 1:
            decisions[0]["id"] = "unknown-target"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"entities": decisions})), finish_reason="stop")]
        )

    monkeypatch.setattr(cr, "dispatch_llm_pre_call", redact)
    monkeypatch.setattr(cr, "completion", complete)
    result = cr.resolve_conflicts([old], [new], custom_update_entities_prompt="Compare memories; contact admin@example.com for 5 issues.")
    assert [r.id for r in result] == (["7"] if event == "ADD" else ["6"])
    assert result[0].incoming_ids == ["7"]
    assert result[0].event == event
    assert len(attempts) == (2 if retry else 1)
    assert old.content == "Email alice@example.com with 6 reports"
    assert new.content == "Email bob@example.com with 8 reports"


@pytest.mark.parametrize(
    "change",
    [
        lambda messages: [],
        lambda messages: [{"role": "user", "content": {}}],
        lambda messages: [{"role": "system", "content": "changed"}],
        lambda messages: [None],
        lambda messages: messages * 2,
    ],
)
def test_conflict_filter_rejects_changed_shape(monkeypatch, change):
    monkeypatch.setattr(cr, "dispatch_llm_pre_call", lambda messages, **kwargs: change(messages))
    with pytest.raises(ValueError, match="must return one user message containing text"):
        cr._redact_content(["sensitive"], "test")


def test_conflict_filter_preserves_nested_content_without_mutating_inputs(monkeypatch):
    value = {"text": ["alice@example.com", 0.8], "optional": None, "id": "private@example.com"}
    monkeypatch.setattr(cr, "dispatch_llm_pre_call", lambda messages, **kwargs: [{**m, "content": "[REDACTED]"} for m in messages])
    assert cr._redact_content([value], "test") == [{"text": ["[REDACTED]", 0.8], "optional": None, "id": "[REDACTED]"}]
    assert value["text"][0] == "alice@example.com"


def test_hook_cannot_supply_operations_or_extra_messages(monkeypatch):
    def redact(messages, **kwargs):
        assert messages == [{"role": "user", "content": "alice@example.com"}]
        return [{"role": "user", "content": "[REDACTED]", "id": "6", "event": "DELETE", "tool_calls": [{"id": "7"}]}]

    monkeypatch.setattr(cr, "dispatch_llm_pre_call", redact)
    assert cr._redact_content("alice@example.com", "test") == "[REDACTED]"


def test_filter_failure_prevents_model_call(monkeypatch):
    def reject(*args, **kwargs):
        raise ValueError("redaction unavailable")

    def unexpected(*args, **kwargs):
        pytest.fail("Model must not receive unfiltered inputs")

    monkeypatch.setattr(cr, "dispatch_llm_pre_call", reject)
    monkeypatch.setattr(cr, "completion", unexpected)
    entity = RecordedEntity(id="6", type="fact", content="alice@example.com", created_at=datetime.now())
    with pytest.raises(ValueError, match="redaction unavailable"):
        cr.resolve_conflicts([], [entity])


def test_conflict_filter_preserves_non_text_values_without_calling_hooks(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("No egress text to filter")

    monkeypatch.setattr(cr, "dispatch_llm_pre_call", unexpected)
    assert cr._redact_content([{"probability": 0.8, "count": 6, "enabled": True, "optional": None}], "test") == [
        {"probability": 0.8, "count": 6, "enabled": True, "optional": None}
    ]
