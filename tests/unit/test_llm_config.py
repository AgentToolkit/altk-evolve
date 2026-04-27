"""Tests for LLM settings compatibility."""

import pytest

from altk_evolve.config.llm import LLMSettings


pytestmark = pytest.mark.unit


def test_guidelines_model_uses_legacy_tips_env_when_new_env_missing(monkeypatch):
    monkeypatch.delenv("EVOLVE_GUIDELINES_MODEL", raising=False)
    monkeypatch.setenv("EVOLVE_TIPS_MODEL", "openai/gpt-4o-mini")
    monkeypatch.delenv("EVOLVE_MODEL_NAME", raising=False)

    settings = LLMSettings()

    assert settings.guidelines_model == "openai/gpt-4o-mini"


def test_guidelines_model_prefers_new_env_over_legacy(monkeypatch):
    monkeypatch.setenv("EVOLVE_GUIDELINES_MODEL", "openai/gpt-5")
    monkeypatch.setenv("EVOLVE_TIPS_MODEL", "openai/gpt-4o-mini")

    settings = LLMSettings()

    assert settings.guidelines_model == "openai/gpt-5"
