"""Consistency metrics honor configured models without implicit extra downloads."""

import pytest

from altk_evolve.config.guidelines import GuidelinesSettings
from altk_evolve.llm.guidelines.consistency_analyzer import consistency_metric as metrics


@pytest.mark.unit
def test_both_metrics_reuse_configured_embedding_model(monkeypatch, mock_sentence_transformer):
    for name in (
        "EVOLVE_CONSISTENCY_EMBEDDING_MODEL_SMALL",
        "EVOLVE_CONSISTENCY_EMBEDDING_MODEL_LARGE",
        "EVOLVE_CONSISTENCY_EMBEDDING_TRUST_REMOTE_CODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(metrics, "guidelines_settings", GuidelinesSettings(_env_file=None))
    monkeypatch.setattr(metrics.milvus_other_settings, "embedding_model", "BAAI/bge-small-en-v1.5")
    small = metrics.get_metric_instance("sbert_small")
    large = metrics.get_metric_instance("sbert_large")
    assert small.sentence_transformer_model is large.sentence_transformer_model
    mock_sentence_transformer.assert_called_once_with("BAAI/bge-small-en-v1.5", trust_remote_code=False)


@pytest.mark.unit
def test_explicit_large_model_preserves_specialized_option(monkeypatch, mock_sentence_transformer):
    monkeypatch.setenv("EVOLVE_CONSISTENCY_EMBEDDING_MODEL_LARGE", "nomic-ai/CodeRankEmbed")
    monkeypatch.setenv("EVOLVE_CONSISTENCY_EMBEDDING_TRUST_REMOTE_CODE", "true")
    monkeypatch.setattr(metrics, "guidelines_settings", GuidelinesSettings(_env_file=None))
    metrics.get_metric_instance("sbert_large")
    mock_sentence_transformer.assert_called_once_with("nomic-ai/CodeRankEmbed", trust_remote_code=True)


@pytest.mark.unit
def test_model_change_does_not_reuse_stale_cached_model(monkeypatch, mock_sentence_transformer):
    settings = GuidelinesSettings(_env_file=None, consistency_embedding_model_small="first-model")
    monkeypatch.setattr(metrics, "guidelines_settings", settings)
    metrics.get_sentence_transformer_small()
    settings.consistency_embedding_model_small = "second-model"
    metrics.get_sentence_transformer_small()
    assert [call.args[0] for call in mock_sentence_transformer.call_args_list] == ["first-model", "second-model"]
