"""Pytest configuration and fixtures."""

import pytest
from unittest.mock import Mock, patch


@pytest.fixture(scope="function", autouse=True)
def mock_sentence_transformer(request, monkeypatch):
    """
    Mock SentenceTransformer for unit tests only to prevent network calls and hanging.

    This fixture automatically patches SentenceTransformer for tests marked with '@pytest.mark.unit',
    preventing it from downloading models or making network calls during unit tests.
    E2E tests and other test types will use the real SentenceTransformer.
    The mock returns a simple Mock object with an encode method that returns dummy embeddings.
    """
    # Only apply the mock for unit tests (check if test has the 'unit' marker)
    if request.node.get_closest_marker("unit"):
        monkeypatch.setenv("EVOLVE_EMBEDDING_PROVIDER", "sentence_transformers")
        with patch("sentence_transformers.SentenceTransformer") as mock_st:
            from altk_evolve.llm.guidelines.consistency_analyzer import consistency_metric

            consistency_metric._load_consistency_model.cache_clear()
            from altk_evolve.embeddings import _load_embedding_model

            _load_embedding_model.cache_clear()

            # Create a mock instance that will be returned when SentenceTransformer() is called
            mock_instance = Mock()
            # Mock the encode method to return dummy embeddings (list of floats)
            import numpy as np

            def _mock_encode(texts, *args, **kwargs):
                if isinstance(texts, list):
                    # Return distinct orthogonal-ish vectors for distinct strings so divergent samples have similarity < 1
                    arr = []
                    for i, t in enumerate(texts):
                        vec = [0.0] * 384
                        # Use character sums to scatter across dimensions
                        dim = abs(hash(t)) % 384
                        vec[dim] = 1.0
                        arr.append(vec)
                    return np.array(arr)
                return np.array([[0.1] * 384])

            def _mock_similarity(e1, e2):
                # Cosine similarity matrix between e1 and e2
                e1_norm = e1 / (np.linalg.norm(e1, axis=-1, keepdims=True) + 1e-9)
                e2_norm = e2 / (np.linalg.norm(e2, axis=-1, keepdims=True) + 1e-9)
                return np.dot(e1_norm, e2_norm.T)

            mock_instance.encode.side_effect = _mock_encode
            mock_instance.similarity.side_effect = _mock_similarity
            mock_st.return_value = mock_instance
            yield mock_st
            consistency_metric._load_consistency_model.cache_clear()
            _load_embedding_model.cache_clear()
    else:
        # For non-unit tests, don't apply the mock
        yield None


@pytest.fixture
def default_trajectory_window(monkeypatch):
    """Pin the trajectory window to its defaults: 50 steps, 2000 characters each, no tail.

    evolve_config reads EVOLVE_TRAJECTORY_MAX_STEPS, _MAX_STEP_CHARS and _TAIL_STEPS from the
    environment and from .env. A test that builds its runtime with GuidelineRuntime.from_settings()
    renders whatever window those set, so an exported EVOLVE_TRAJECTORY_MAX_STEPS=4 elides
    the trajectory and moves the steps a subtask range points at. Tests that need a different
    window still set it themselves, through an explicit GuidelineRuntime or monkeypatch.
    """
    from altk_evolve.config.evolve import evolve_config

    monkeypatch.setattr(evolve_config, "trajectory_max_steps", 50)
    monkeypatch.setattr(evolve_config, "trajectory_max_step_chars", 2000)
    monkeypatch.setattr(evolve_config, "trajectory_tail_steps", 0)


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run End-to-End infrastructure tests (skipped by default)",
    )


def pytest_configure(config):
    """Override marker filter when relevant flags are passed."""
    new_expr = config.getoption("markexpr", default="")

    if config.getoption("--run-e2e"):
        # Remove "not e2e" from the expression
        new_expr = new_expr.replace("not e2e and ", "").replace(" and not e2e", "").replace("not e2e", "")

    config.option.markexpr = new_expr.strip()
