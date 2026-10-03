"""READI device selection must not alter the embedding application's context."""

from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
import sys

import pytest

from altk_evolve.hooks.plugins import readi

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "platform,device,expected",
    [("darwin", "auto", "numpy"), ("linux", "auto", "cupy"), ("darwin", "cpu", "numpy"), ("linux", "cuda", "cupy")],
)
def test_device_scope_covers_loading_and_inference_and_restores_on_error(monkeypatch, platform, device, expected):
    host = SimpleNamespace(name="host")
    current = ContextVar("ops", default=host)
    seen = []

    @contextmanager
    def use_ops(name):
        token = current.set(SimpleNamespace(name=name))
        try:
            yield
        finally:
            current.reset(token)

    def load(name):
        seen.append(("load", current.get().name))
        return object()

    def gpu():
        current.set(SimpleNamespace(name="cupy"))

    class Base:
        def __init__(self, mapping):
            pass

    class Extractor(Base):
        def __init__(self, *args):
            raise AssertionError("Do not call READI device-selecting constructor")

        def extract(self, text):
            seen.append(("extract", current.get().name))
            if text == "fail":
                raise RuntimeError("inference failed")
            return []

    monkeypatch.setattr(sys, "platform", platform)
    for name, module in {
        "spacy": SimpleNamespace(load=load, prefer_gpu=gpu, require_gpu=gpu),
        "thinc.api": SimpleNamespace(get_current_ops=current.get, set_current_ops=current.set, use_ops=use_ops),
        "risk_assessment.classification.unstructured": SimpleNamespace(EntityExtractor=Base),
        "risk_assessment.classification.unstructured.spacy": SimpleNamespace(SpacyEntityExtractor=Extractor),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    extractor = readi._build_spacy_extractor("test-model", device)
    assert current.get() is host
    assert extractor.extract("ok") == []
    assert current.get() is host
    with pytest.raises(RuntimeError):
        extractor.extract("fail")
    assert current.get() is host
    assert seen == [("load", expected), ("extract", expected), ("extract", expected)]


@pytest.mark.parametrize("extractor,device", [("spacy", "invalid"), ("hf", "cpu"), ("default", "cuda")])
def test_invalid_device_config_fails_before_loading_models(extractor, device):
    with pytest.raises(ValueError, match="readi_device"):
        readi.build_readi_detector(extractor=extractor, device=device)
