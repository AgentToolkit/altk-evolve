"""Network/model-cache integration check for the new default runtime."""

import numpy as np
import pytest


@pytest.mark.e2e
def test_minilm_runtime_parity(monkeypatch):
    pytest.importorskip("fastembed")
    pytest.importorskip("sentence_transformers")
    from altk_evolve.embeddings import get_embedding_model

    texts = ["Retain useful memories.", "Retain useful memories.", "A different document."]
    model_name = "sentence-transformers/all-MiniLM-L6-v2"
    monkeypatch.setenv("EVOLVE_EMBEDDING_PROVIDER", "sentence_transformers")
    previous = get_embedding_model(model_name).encode(texts, normalize_embeddings=True)
    monkeypatch.setenv("EVOLVE_EMBEDDING_PROVIDER", "fastembed")
    current = get_embedding_model(model_name).encode(texts, normalize_embeddings=True)
    assert previous.shape == current.shape == (3, 384)
    np.testing.assert_allclose((previous * current).sum(axis=1), 1, atol=1e-3)
