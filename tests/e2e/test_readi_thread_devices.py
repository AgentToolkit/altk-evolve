"""Opt-in real READI model regression; no downloads during test collection."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.e2e


def test_spacy_redaction_across_threads_restores_device_context():
    pytest.importorskip("risk_assessment")
    if importlib.util.find_spec("en_core_web_trf") is None:
        pytest.skip("Install en_core_web_trf to run the real READI regression")
    import torch
    from thinc.api import get_current_ops
    from altk_evolve.hooks.plugins.readi import build_readi_detector, redact_spans

    available = torch.backends.mps.is_available()
    detector = build_readi_detector(extractor="spacy", model="en_core_web_trf")

    def redact(_):
        before = get_current_ops()
        text = "John Smith lives in New York."
        redacted = redact_spans(text, detector(text))
        assert get_current_ops() is before
        assert "John Smith" not in redacted
        assert "New York" not in redacted
        return redacted

    first = redact(0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(redact, range(8))) == [first] * 8
    assert torch.backends.mps.is_available() == available
