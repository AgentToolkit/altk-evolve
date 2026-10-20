"""Consistency embedding defaults and overrides."""

import logging

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
    # Pin the literal rather than re-asserting MINILM_MODEL against itself — a
    # silent change to the default model should fail this test, not pass it.
    assert MINILM_MODEL == "sentence-transformers/all-MiniLM-L6-v2"
    mock_sentence_transformer.assert_called_once_with("sentence-transformers/all-MiniLM-L6-v2", trust_remote_code=False)


@pytest.mark.unit
def test_sbert_large_in_get_metric_instance_names_replacement():
    with pytest.raises(ValueError, match=r"sbert_large.*sbert_small"):
        metrics.get_metric_instance("sbert_large")


@pytest.mark.unit
def test_sbert_large_in_get_consistency_by_metric_names_replacement():
    # Fail-open regression guard: before this fix, sbert_large fell through to
    # the unknown-metric path and returned (-1, -1), which the aggregator drops
    # silently. Any config that still names it must raise.
    with pytest.raises(ValueError, match=r"sbert_large.*sbert_small"):
        metrics.get_consistency_by_metric(["a b", "a b"], "sbert_large")


@pytest.mark.unit
def test_removed_embedding_env_var_warns(monkeypatch, caplog):
    monkeypatch.setenv("EVOLVE_CONSISTENCY_EMBEDDING_MODEL_LARGE", "nomic-ai/CodeRankEmbed")
    with caplog.at_level(logging.WARNING, logger="altk_evolve.config.guidelines"):
        GuidelinesSettings(_env_file=None)
    assert any("EVOLVE_CONSISTENCY_EMBEDDING_MODEL_LARGE" in record.message for record in caplog.records)
    assert any("sbert_small" in record.message for record in caplog.records)


@pytest.mark.unit
def test_removed_embedding_env_var_in_dotenv_only_still_warns(tmp_path, monkeypatch, caplog):
    """A retired name kept only in .env is also loaded by pydantic-settings and dropped
    by extra='ignore', so the migration warning has to see dotenv values, not just os.environ."""
    monkeypatch.delenv("EVOLVE_CONSISTENCY_EMBEDDING_MODEL_LARGE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("EVOLVE_CONSISTENCY_EMBEDDING_MODEL_LARGE=nomic-ai/CodeRankEmbed\n")
    with caplog.at_level(logging.WARNING, logger="altk_evolve.config.guidelines"):
        GuidelinesSettings(_env_file=str(env_file))
    assert any("EVOLVE_CONSISTENCY_EMBEDDING_MODEL_LARGE" in record.message for record in caplog.records)
    assert any("sbert_small" in record.message for record in caplog.records)


@pytest.mark.unit
def test_model_change_does_not_reuse_stale_cached_model(monkeypatch, mock_sentence_transformer):
    settings = GuidelinesSettings(_env_file=None, consistency_embedding_model_small="first-model")
    monkeypatch.setattr(metrics, "guidelines_settings", settings)
    metrics.get_sentence_transformer_small()
    settings.consistency_embedding_model_small = "second-model"
    metrics.get_sentence_transformer_small()
    assert [call.args[0] for call in mock_sentence_transformer.call_args_list] == ["first-model", "second-model"]
