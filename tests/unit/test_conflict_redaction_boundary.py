"""Conflict routing must survive redactors that mask numbers in free text."""

import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from altk_evolve.llm.conflict_resolution import conflict_resolution as cr
from altk_evolve.schema.core import RecordedEntity

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def active_hooks(monkeypatch):
    monkeypatch.setattr(cr, "hooks_active", lambda hook: True)


@pytest.mark.parametrize("event", ["ADD", "UPDATE", "DELETE", "NONE"])
@pytest.mark.parametrize("retry", [False, True])
def test_conflict_prompt_keeps_ids_but_redacts_content(monkeypatch, event, retry):
    old = RecordedEntity(id="6", type="guideline", content="Email alice@example.com with 6 reports", created_at=datetime.now())
    new = RecordedEntity(id="7", type="guideline", content="Email bob@example.com with 8 reports", created_at=datetime.now())

    seen_content = []

    def redact(messages, **kwargs):
        seen_content.extend(m["content"] for m in messages)
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
        assert "alice@example.com" not in prompt and "bob@example.com" not in prompt
        assert "Compare memories; contact [REDACTED] for [REDACTED] issues." in prompt
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
    assert seen_content == [old.content, new.content, "Compare memories; contact admin@example.com for 5 issues."]
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
        cr._apply_llm_pre_call(["sensitive"], "test")


@pytest.mark.parametrize(
    "content",
    [
        {"alice@example.com": "contact", "phone": 2025550147},
        [{"nested": {"alice@example.com": [2025550147]}}],
    ],
)
def test_content_keys_and_numbers_reach_hook_without_mutating_inputs(monkeypatch, content):
    original = json.dumps(content)

    def redact(messages, **kwargs):
        assert messages == [{"role": "user", "content": original}]
        return [{"role": "user", "content": original.replace("alice@example.com", "[REDACTED]").replace("2025550147", "[REDACTED]")}]

    monkeypatch.setattr(cr, "dispatch_llm_pre_call", redact)
    result = cr._apply_llm_pre_call(content, "test")
    assert "alice@example.com" not in result and "2025550147" not in result
    assert "[REDACTED]" in result
    assert json.dumps(content) == original


def test_hook_cannot_supply_operations_or_extra_messages(monkeypatch):
    def redact(messages, **kwargs):
        assert messages == [{"role": "user", "content": "alice@example.com"}]
        return [{"role": "user", "content": "[REDACTED]", "id": "6", "event": "DELETE", "tool_calls": [{"id": "7"}]}]

    monkeypatch.setattr(cr, "dispatch_llm_pre_call", redact)
    assert cr._apply_llm_pre_call("alice@example.com", "test") == "[REDACTED]"


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


def test_unchanged_structured_content_uses_storage_serialization(monkeypatch):
    monkeypatch.setattr(cr, "dispatch_llm_pre_call", lambda messages, **kwargs: messages)
    content = {"probability": 0.8, "count": 6, "enabled": True, "optional": None}
    assert cr._apply_llm_pre_call(content, "test") == content


@pytest.mark.parametrize("content", [{"name": "José", "weight": 0.5}, ["José", {"flags": ["-r"]}], "123"])
def test_no_hook_prompt_preserves_original_content(monkeypatch, content):
    monkeypatch.setattr(cr, "hooks_active", lambda hook: False)
    monkeypatch.setattr(cr, "dispatch_llm_pre_call", lambda *a, **kw: pytest.fail("inactive hook dispatched"))
    entity = RecordedEntity(id="7", type="fact", content=content, created_at=datetime.now())
    expected = cr.get_update_entities_messages([], cr.SimpleEntity.from_recorded_entities([entity]), "custom instructions")

    def complete(**kwargs):
        assert kwargs["messages"][0]["content"].split("\nFor each decision include incoming_ids:")[0] == expected
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"entities": [{"id": "7", "type": "fact", "content": content, "event": "ADD"}]})
                    ),
                    finish_reason="stop",
                )
            ]
        )

    monkeypatch.setattr(cr, "completion", complete)
    assert cr.resolve_conflicts([], [entity], custom_update_entities_prompt="custom instructions")[0].content == content


def test_valid_redacted_json_preserves_structure(monkeypatch):
    monkeypatch.setattr(
        cr,
        "dispatch_llm_pre_call",
        lambda messages, **kw: [{"role": "user", "content": messages[0]["content"].replace("alice@example.com", "[REDACTED]")}],
    )
    assert cr._apply_llm_pre_call({"email": "alice@example.com"}, "test") == {"email": "[REDACTED]"}
    assert cr._apply_llm_pre_call("123", "test") == "123"


def test_duplicate_redacted_keys_preserve_both_values_as_text(monkeypatch):
    def redact(messages, **kwargs):
        return [{"role": "user", "content": re.sub(r"\w+@example\.com", "[REDACTED]", messages[0]["content"])}]

    monkeypatch.setattr(cr, "dispatch_llm_pre_call", redact)
    content = {"alice@example.com": "first", "bob@example.com": "second"}
    result = cr._apply_llm_pre_call(content, "test")
    assert isinstance(result, str)
    assert '"first"' in result and '"second"' in result
    assert result.count('"[REDACTED]"') == 2
