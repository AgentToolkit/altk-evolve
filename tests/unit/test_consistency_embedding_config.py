"""Consistency defaults preserve the specialized models with either provider."""

import pytest
from altk_evolve.embedding_assets import CODERANK_REVISION

from altk_evolve.config.guidelines import GuidelinesSettings
from altk_evolve.llm.guidelines.consistency_analyzer import consistency_metric as metrics


@pytest.mark.unit
def test_default_metrics_use_minilm_and_pinned_coderank(monkeypatch, mock_sentence_transformer):
    for name in (
        "EVOLVE_CONSISTENCY_EMBEDDING_MODEL_SMALL",
        "EVOLVE_CONSISTENCY_EMBEDDING_MODEL_LARGE",
        "EVOLVE_CONSISTENCY_EMBEDDING_TRUST_REMOTE_CODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(metrics, "guidelines_settings", GuidelinesSettings(_env_file=None))
    from altk_evolve.embedding_assets import CODERANK_MODEL, CODERANK_REVISION, MINILM_MODEL
    from unittest.mock import call

    metrics.get_metric_instance("sbert_small")
    metrics.get_metric_instance("sbert_large")
    assert mock_sentence_transformer.call_args_list == [
        call(MINILM_MODEL, trust_remote_code=False),
        call(CODERANK_MODEL, revision=CODERANK_REVISION, trust_remote_code=True),
    ]


@pytest.mark.unit
def test_explicit_large_model_preserves_specialized_option(monkeypatch, mock_sentence_transformer):
    monkeypatch.setenv("EVOLVE_CONSISTENCY_EMBEDDING_MODEL_LARGE", "nomic-ai/CodeRankEmbed")
    monkeypatch.setenv("EVOLVE_CONSISTENCY_EMBEDDING_TRUST_REMOTE_CODE", "true")
    monkeypatch.setattr(metrics, "guidelines_settings", GuidelinesSettings(_env_file=None))
    metrics.get_metric_instance("sbert_large")
    mock_sentence_transformer.assert_called_once_with("nomic-ai/CodeRankEmbed", revision=CODERANK_REVISION, trust_remote_code=True)


@pytest.mark.unit
def test_model_change_does_not_reuse_stale_cached_model(monkeypatch, mock_sentence_transformer):
    settings = GuidelinesSettings(_env_file=None, consistency_embedding_model_small="first-model")
    monkeypatch.setattr(metrics, "guidelines_settings", settings)
    metrics.get_sentence_transformer_small()
    settings.consistency_embedding_model_small = "second-model"
    metrics.get_sentence_transformer_small()
    assert [call.args[0] for call in mock_sentence_transformer.call_args_list] == ["first-model", "second-model"]
