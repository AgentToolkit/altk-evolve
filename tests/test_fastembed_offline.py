"""Run with a preloaded BGE cache and networking disabled."""

import os

import numpy as np
import pytest


@pytest.mark.e2e
@pytest.mark.skipif(not os.environ.get("FASTEMBED_CACHE_PATH"), reason="Requires preloaded FastEmbed cache")
def test_fastembed_cached_embeddings_and_consistency(monkeypatch):
    pytest.importorskip("fastembed")
    monkeypatch.setenv("EVOLVE_EMBEDDING_PROVIDER", "fastembed")
    from altk_evolve.embeddings import get_embedding_model
    from altk_evolve.llm.guidelines.consistency_analyzer import consistency_metric as metrics

    monkeypatch.setattr(metrics.guidelines_settings, "consistency_embedding_model_small", "BAAI/bge-small-en-v1.5")
    monkeypatch.setattr(metrics.guidelines_settings, "consistency_embedding_model_large", "BAAI/bge-small-en-v1.5")
    metrics._load_consistency_model.cache_clear()
    model = get_embedding_model("BAAI/bge-small-en-v1.5")
    vectors = model.encode(["customer impact first", "lead with customer impact"])
    assert vectors.shape == (2, 384)
    assert np.isfinite(vectors).all()
    assert model.similarity(vectors, vectors)[0, 0] == pytest.approx(1, abs=1e-6)
    for name in ("sbert_small", "sbert_large"):
        metric = metrics.get_metric_instance(name)
        assert metric.sentence_transformer_model is model
        consistency, distance = metric.get_consistency_and_distance(["same response", "same response"])
        assert consistency == pytest.approx(1, abs=1e-6)
        assert 0 <= distance <= 1
