"""Provider selection and the embedding contract used throughout Evolve."""

import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from altk_evolve.embeddings import get_embedding_model


@pytest.fixture
def fastembed(monkeypatch):
    class FakeTextEmbedding:
        calls = []

        def __init__(self, **kwargs):
            self.calls.append(kwargs)

        @classmethod
        def list_supported_models(cls):
            return [{"model": "BAAI/bge-small-en-v1.5", "dim": 3}]

        def embed(self, texts, **kwargs):
            assert kwargs == {"parallel": None}
            for text in texts:
                yield [3, 4, 0] if text == "same" else [0, 0, 2]

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FakeTextEmbedding))
    monkeypatch.setenv("EVOLVE_EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", "/existing/cache")
    return FakeTextEmbedding


@pytest.mark.unit
def test_fastembed_contract_and_cache(fastembed, mock_sentence_transformer):
    model = get_embedding_model("BAAI/bge-small-en-v1.5")
    assert model is get_embedding_model("BAAI/bge-small-en-v1.5")
    assert fastembed.calls == [{"model_name": "BAAI/bge-small-en-v1.5", "cache_dir": "/existing/cache"}]
    assert model.get_sentence_embedding_dimension() == 3
    assert model.encode("same").shape == (3,)
    assert model.encode([]).shape == (0, 3)
    vectors = model.encode(["same", "other"], normalize_embeddings=True)
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), [1, 1])
    np.testing.assert_allclose(model.similarity(vectors, vectors), np.eye(2), atol=1e-6)
    assert np.isfinite(model.similarity([[0, 0, 0]], vectors)).all()
    mock_sentence_transformer.assert_not_called()


@pytest.mark.unit
def test_fastembed_used_by_consistency_and_guideline_selection(fastembed, monkeypatch, mock_sentence_transformer):
    from altk_evolve.config.guidelines import GuidelinesSettings
    from altk_evolve.llm.guidelines.consistency_analyzer import consistency_metric as metrics
    from altk_evolve.llm.guidelines import clustering, retrieval
    from altk_evolve.schema.core import RecordedEntity

    monkeypatch.setattr(metrics, "guidelines_settings", GuidelinesSettings(_env_file=None))
    monkeypatch.setattr(metrics.guidelines_settings, "consistency_embedding_model_small", "BAAI/bge-small-en-v1.5")
    monkeypatch.setattr(metrics.guidelines_settings, "consistency_embedding_model_large", "BAAI/bge-small-en-v1.5")
    monkeypatch.setattr(
        retrieval, "_embed", lambda texts, _: get_embedding_model("BAAI/bge-small-en-v1.5").encode(texts, normalize_embeddings=True)
    )
    clustering._get_sentence_transformer.cache_clear()
    try:
        for name in ("sbert_small", "sbert_large"):
            metric = metrics.get_metric_instance(name)
            score, distance = metric.get_consistency_and_distance(["same", "same"])
            assert score == pytest.approx(1)
            assert distance == pytest.approx(0)
            assert metric.get_distance_from_chosen_trajectory(["same", "other"], "same") == pytest.approx([0, 1])
        entities = [
            RecordedEntity(
                created_at=datetime.now(timezone.utc), id="1", type="guideline", content="same", metadata={"task_description": "same"}
            ),
            RecordedEntity(
                created_at=datetime.now(timezone.utc), id="2", type="guideline", content="other", metadata={"task_description": "other"}
            ),
        ]
        selected = retrieval.select_guidelines(entities, "same", top_k=1)
        assert selected.retrieved[0].id == "1"
        assert clustering.cluster_entities(entities, embedding_model="BAAI/bge-small-en-v1.5") == []
        assert len(fastembed.calls) == 1
        mock_sentence_transformer.assert_not_called()
    finally:
        clustering._get_sentence_transformer.cache_clear()


@pytest.mark.unit
def test_fastembed_rejects_remote_code(fastembed):
    with pytest.raises(ValueError, match="trust_remote_code"):
        get_embedding_model("BAAI/bge-small-en-v1.5", trust_remote_code=True)


@pytest.mark.unit
def test_sentence_transformers_stays_default(monkeypatch, mock_sentence_transformer):
    monkeypatch.delenv("EVOLVE_EMBEDDING_PROVIDER", raising=False)
    get_embedding_model("custom-model", trust_remote_code=True)
    mock_sentence_transformer.assert_called_once_with("custom-model", trust_remote_code=True)


@pytest.mark.unit
def test_fastembed_accepts_case_insensitive_model_name(fastembed):
    model = get_embedding_model("baai/BGE-small-en-v1.5")
    assert model.get_sentence_embedding_dimension() == 3


@pytest.mark.unit
def test_fastembed_cache_path_from_dotenv(fastembed, monkeypatch, tmp_path):
    from altk_evolve.embedding_assets import coderank_directory

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH")
    (tmp_path / ".env").write_text("EVOLVE_EMBEDDING_PROVIDER=fastembed\nFASTEMBED_CACHE_PATH=/dotenv/cache\n")
    get_embedding_model("BAAI/bge-small-en-v1.5")
    assert fastembed.calls[-1]["cache_dir"] == "/dotenv/cache"
    assert coderank_directory().parent.as_posix() == "/dotenv/cache"
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", "/environment/cache")
    get_embedding_model("BAAI/bge-small-en-v1.5")
    assert fastembed.calls[-1]["cache_dir"] == "/environment/cache"
