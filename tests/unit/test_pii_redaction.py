"""Tests for PII redaction (issue #275).

Pure-logic tests run everywhere; tests that exercise the real CPEX detector are
gated on ``cpex_pii_filter`` being importable (the ``[pii]`` extra).
"""

import importlib.util

import pytest

from altk_evolve.config.pii import PIIConfig
from altk_evolve.pii.redaction import (
    DEFAULT_ENTITIES,
    CpexRegexRedactor,
    NullRedactor,
    PIIRedactor,
    ReadiSemanticRedactor,
    detector_options,
    get_redactor,
)

pytestmark = pytest.mark.unit

HAS_CPEX = importlib.util.find_spec("cpex_pii_filter") is not None
requires_cpex = pytest.mark.skipif(not HAS_CPEX, reason="requires the [pii] extra (cpex-pii-filter)")

HAS_READI = importlib.util.find_spec("risk_assessment") is not None
requires_readi = pytest.mark.skipif(not HAS_READI, reason="requires the [readi] extra (readi-privacy)")


class _UpperRedactor(PIIRedactor):
    """Test double: 'redacts' by uppercasing, to check structural recursion."""

    def redact(self, text: str) -> str:
        return text.upper()


# ── pure logic ────────────────────────────────────────────────────────


def test_detector_options_maps_entities_and_drops_unknown():
    opts = detector_options(PIIConfig(enabled=True, entities=["email", "phone", "not_a_thing"], mask_strategy="hash"))
    assert opts["detect_email"] is True
    assert opts["detect_phone"] is True
    assert "detect_not_a_thing" not in opts
    assert opts["default_mask_strategy"] == "hash"
    assert opts["redaction_text"] == "[REDACTED]"


def test_detector_options_defaults_when_no_entities():
    opts = detector_options({})
    for entity in DEFAULT_ENTITIES:
        assert opts[f"detect_{entity}"] is True


def test_get_redactor_disabled_returns_null():
    assert isinstance(get_redactor(None), NullRedactor)
    assert isinstance(get_redactor(PIIConfig(enabled=False)), NullRedactor)


@requires_readi
def test_get_redactor_semantic_returns_readi_backend():
    # Construction is cheap: it validates the import but defers the model load to
    # the first detect() call, so this does not download en_core_web_trf.
    r = get_redactor(PIIConfig(enabled=True, mode="semantic"))
    assert isinstance(r, ReadiSemanticRedactor)
    assert r._extractor == "default"  # default = READI's spaCy-English pipeline


@requires_readi
def test_semantic_extractor_and_model_are_configurable():
    # Still cheap — no analyzer/model is built until the first detect().
    r = get_redactor(PIIConfig(enabled=True, mode="semantic", readi_extractor="hf", readi_model="some/ner-model", readi_language="ja"))
    assert r._extractor == "hf"
    assert r._model == "some/ner-model"
    assert r._language == "ja"


def test_null_redactor_is_passthrough():
    r = NullRedactor()
    assert r.redact("john@acme.com") == "john@acme.com"
    assert r.redact_value({"a": ["john@acme.com"]}) == {"a": ["john@acme.com"]}


def test_redact_value_walks_nested_structure_and_preserves_scalars():
    r = _UpperRedactor()
    value = {"note": "hi", "tags": ["a", "b"], "count": 3, "ok": True, "nada": None}
    assert r.redact_value(value) == {"note": "HI", "tags": ["A", "B"], "count": 3, "ok": True, "nada": None}


# ── real CPEX backend ─────────────────────────────────────────────────


@requires_cpex
def test_get_redactor_regex_returns_cpex_and_carries_metadata_flag():
    r = get_redactor(PIIConfig(enabled=True, mode="regex", redact_metadata=True))
    assert isinstance(r, CpexRegexRedactor)
    assert r.redact_metadata is True


@requires_cpex
def test_cpex_redactor_masks_structured_pii():
    r = get_redactor(PIIConfig(enabled=True, entities=["email", "phone", "ssn"]))
    masked = r.redact("Reach john@acme.com or 555-123-4567, SSN 123-45-6789.")
    assert "john@acme.com" not in masked
    assert "555-123-4567" not in masked
    assert "123-45-6789" not in masked
    assert "[REDACTED]" in masked


@requires_cpex
def test_cpex_redactor_redacts_nested_content():
    r = get_redactor(PIIConfig(enabled=True, entities=["email"]))
    out = r.redact_value({"messages": ["ping john@acme.com", "no pii here"]})
    assert "john@acme.com" not in out["messages"][0]
    assert out["messages"][1] == "no pii here"


@requires_cpex
def test_client_redacts_pii_at_the_write_chokepoint(tmp_path):
    """End-to-end: PII never reaches the persisted store (issue #275)."""
    from altk_evolve.backend.filesystem import FilesystemSettings
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.schema.core import Entity

    cfg = EvolveConfig(
        backend="filesystem",
        settings=FilesystemSettings(data_dir=str(tmp_path)),
        pii=PIIConfig(enabled=True, entities=["email"]),
    )
    client = EvolveClient(cfg)
    client.create_namespace("ns")
    client.update_entities(
        "ns",
        [Entity(content="ping me at john@acme.com", type="guideline")],
        enable_conflict_resolution=False,
    )

    stored = client.get_all_entities("ns")
    assert len(stored) == 1
    assert "john@acme.com" not in stored[0].content
    assert "[REDACTED]" in stored[0].content
