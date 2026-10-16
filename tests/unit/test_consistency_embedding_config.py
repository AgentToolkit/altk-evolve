"""Consistency embedding defaults and overrides."""

import pytest

from altk_evolve.config.guidelines import GuidelinesSettings
from altk_evolve.llm.guidelines.consistency_analyzer import consistency_metric as metrics


@pytest.mark.unit
def test_default_metric_uses_minilm(monkeypatch, mock_sentence_transformer):
    for name in (
        "EVOLVE_CONSISTENCY_EMBEDDING_MODEL_SMALL",
        "EVOLVE_CONSISTENCY_EMBEDDING_TRUST_REMOTE_CODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(metrics, "guidelines_settings", GuidelinesSettings(_env_file=None))
    from altk_evolve.embeddings import MINILM_MODEL

    metrics.get_metric_instance("sbert_small")
    mock_sentence_transformer.assert_called_once_with(MINILM_MODEL, trust_remote_code=False)


@pytest.mark.unit
def test_sbert_large_is_not_supported():
    with pytest.raises(ValueError, match="sbert_large"):
        metrics.get_metric_instance("sbert_large")


@pytest.mark.unit
def test_model_change_does_not_reuse_stale_cached_model(monkeypatch, mock_sentence_transformer):
    settings = GuidelinesSettings(_env_file=None, consistency_embedding_model_small="first-model")
    monkeypatch.setattr(metrics, "guidelines_settings", settings)
    metrics.get_sentence_transformer_small()
    settings.consistency_embedding_model_small = "second-model"
    metrics.get_sentence_transformer_small()
    assert [call.args[0] for call in mock_sentence_transformer.call_args_list] == ["first-model", "second-model"]
