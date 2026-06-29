"""Tests for the evolve-lite plugin PII redactor (plugin-source/lib/pii.py).

Imported directly from plugin-source (the source of truth), like
test_audit_recall.py. Pure-logic tests run everywhere; the real-CPEX tests are
gated on cpex-pii-filter being importable.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "plugin-source" / "lib"))
import pii  # noqa: E402

pytestmark = [pytest.mark.platform_integrations, pytest.mark.unit]

HAS_CPEX = importlib.util.find_spec("cpex_pii_filter") is not None
requires_cpex = pytest.mark.skipif(not HAS_CPEX, reason="requires cpex-pii-filter")


class _UpperRedactor:
    def redact(self, text):
        return text.upper()


def test_detector_options_maps_and_drops_unknown():
    opts = pii.detector_options({"entities": ["email", "nope"], "mask_strategy": "hash"})
    assert opts["detect_email"] is True
    assert "detect_nope" not in opts
    assert opts["default_mask_strategy"] == "hash"


def test_get_redactor_disabled_returns_null():
    assert isinstance(pii.get_redactor({}), pii.NullRedactor)
    assert isinstance(pii.get_redactor({"pii": {"enabled": False}}), pii.NullRedactor)


def test_get_redactor_semantic_is_a_seam():
    with pytest.raises(NotImplementedError, match="semantic"):
        pii.get_redactor({"pii": {"enabled": True, "mode": "semantic"}})


def test_redact_entity_fields_scrubs_in_place():
    entity = {"content": "ping me", "trigger": "when x", "rationale": None}
    pii.redact_entity_fields(entity, _UpperRedactor())
    assert entity["content"] == "PING ME"
    assert entity["trigger"] == "WHEN X"
    assert entity["rationale"] is None  # missing/None fields untouched


@requires_cpex
def test_cpex_redactor_masks_email():
    r = pii.get_redactor({"pii": {"enabled": True, "entities": ["email"]}})
    assert isinstance(r, pii.CpexRegexRedactor)
    masked = r.redact("write to john@acme.com please")
    assert "john@acme.com" not in masked
    assert "[REDACTED]" in masked
