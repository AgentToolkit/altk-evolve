"""Pytest configuration and fixtures."""

import pytest
from unittest.mock import Mock, patch


@pytest.fixture(scope="function", autouse=True)
def mock_sentence_transformer(request):
    """
    Mock SentenceTransformer for unit tests only to prevent network calls and hanging.

    This fixture automatically patches SentenceTransformer for tests marked with '@pytest.mark.unit',
    preventing it from downloading models or making network calls during unit tests.
    E2E tests and other test types will use the real SentenceTransformer.
    The mock returns a simple Mock object with an encode method that returns dummy embeddings.
    """
    # Only apply the mock for unit tests (check if test has the 'unit' marker)
    if request.node.get_closest_marker("unit"):
        with patch("sentence_transformers.SentenceTransformer") as mock_st:
            # Create a mock instance that will be returned when SentenceTransformer() is called
            mock_instance = Mock()
            # Mock the encode method to return dummy embeddings (list of floats)
            mock_instance.encode.return_value = [[0.1] * 384]  # 384-dimensional dummy embedding
            mock_st.return_value = mock_instance
            yield mock_st
    else:
        # For non-unit tests, don't apply the mock
        yield None


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
