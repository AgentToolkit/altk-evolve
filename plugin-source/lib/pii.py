"""PII redaction for evolve-lite plugin scripts (issue #275).

Strips PII out of memory content before it is written to ``.evolve/`` by the
save path. Mirrors the redaction in the ``altk_evolve`` package, but for the
plugin world: stdlib-only and dependency-optional, because these scripts run in
the host's Python (a Claude Code / Codex / Bob hook), where nothing beyond the
standard library is guaranteed.

The only detection backend is CPEX's regex detector (``cpex-pii-filter`` /
``PIIDetectorRust``), imported lazily. When it is not installed the factory
returns a no-op redactor (warning to stderr when redaction was explicitly
enabled), so the save path never breaks. ``semantic`` mode is a documented seam.

Config lives under a ``pii:`` block in ``evolve.config.yaml`` (read via
``config.load_config``)::

    pii:
      enabled: true
      mode: regex
      entities: [ssn, credit_card, email, phone, ip_address]
      mask_strategy: redact
      redaction_text: "[REDACTED]"
"""

import sys

# Entity flags supported by cpex-pii-filter's regex detector, mapped to its
# ``detect_<name>`` boolean options.
SUPPORTED_ENTITIES = (
    "ssn",
    "bsn",
    "credit_card",
    "email",
    "phone",
    "ip_address",
    "date_of_birth",
    "passport",
    "driver_license",
    "bank_account",
    "medical_record",
)

DEFAULT_ENTITIES = ("ssn", "credit_card", "email", "phone", "ip_address")
DEFAULT_MASK_STRATEGY = "redact"
DEFAULT_REDACTION_TEXT = "[REDACTED]"


class NullRedactor:
    """No-op redactor: returns input unchanged."""

    def redact(self, text):
        return text


def detector_options(pii_cfg):
    """Map a ``pii:`` config dict to ``PIIDetectorRust`` constructor options."""
    pii_cfg = pii_cfg or {}
    entities = pii_cfg.get("entities") or list(DEFAULT_ENTITIES)
    if isinstance(entities, str):
        entities = [e.strip() for e in entities.split(",") if e.strip()]
    opts = {f"detect_{e}": True for e in entities if e in SUPPORTED_ENTITIES}
    opts["default_mask_strategy"] = pii_cfg.get("mask_strategy", DEFAULT_MASK_STRATEGY)
    opts["redaction_text"] = pii_cfg.get("redaction_text", DEFAULT_REDACTION_TEXT)
    if pii_cfg.get("custom_patterns"):
        opts["custom_patterns"] = pii_cfg["custom_patterns"]
    if pii_cfg.get("whitelist_patterns"):
        opts["whitelist_patterns"] = pii_cfg["whitelist_patterns"]
    return opts


class CpexRegexRedactor:
    """Regex-based redactor backed by ``cpex-pii-filter``'s ``PIIDetectorRust``."""

    def __init__(self, pii_cfg=None):
        from cpex_pii_filter import PIIDetectorRust  # lazy: optional dependency

        self._detector = PIIDetectorRust(detector_options(pii_cfg))

    def redact(self, text):
        if not text:
            return text
        return self._detector.mask(text, self._detector.detect(text))


def get_redactor(config):
    """Return a redactor for the given evolve config dict.

    - PII disabled / no ``pii`` block -> :class:`NullRedactor`.
    - ``mode: regex`` (default) -> :class:`CpexRegexRedactor`, or a
      :class:`NullRedactor` (with an stderr warning) when ``cpex-pii-filter``
      is not installed in the host environment.
    - ``mode: semantic`` -> :class:`NotImplementedError` (documented seam;
      CPEX ships no semantic detector).
    """
    pii_cfg = (config or {}).get("pii") or {}
    if not pii_cfg.get("enabled"):
        return NullRedactor()

    mode = str(pii_cfg.get("mode") or "regex").strip().lower()
    if mode == "regex":
        try:
            return CpexRegexRedactor(pii_cfg)
        except ImportError:
            print(
                "evolve-lite: pii.enabled is set but 'cpex-pii-filter' is not "
                "installed in this environment; PII will NOT be redacted. "
                "Install it with `pip install cpex-pii-filter`.",
                file=sys.stderr,
            )
            return NullRedactor()
    if mode == "semantic":
        raise NotImplementedError(
            "pii.mode 'semantic' is not implemented. CPEX ships only a regex "
            "detector (cpex-pii-filter). A semantic/embedding backend is the "
            "documented seam here."
        )
    raise ValueError(f"Unknown pii.mode: {mode!r} (expected 'regex' or 'semantic')")


def redact_entity_fields(entity, redactor, fields=("content", "trigger", "rationale")):
    """Redact the given text fields of an *entity* dict in place; returns it."""
    for key in fields:
        val = entity.get(key)
        if isinstance(val, str) and val:
            entity[key] = redactor.redact(val)
    return entity


if __name__ == "__main__":
    # Self-test (pure paths only; the CPEX backend is covered by pytest).
    assert isinstance(get_redactor({}), NullRedactor)
    assert isinstance(get_redactor({"pii": {"enabled": False}}), NullRedactor)
    assert NullRedactor().redact("john@acme.com") == "john@acme.com"

    opts = detector_options({"entities": ["email", "bogus"], "mask_strategy": "hash"})
    assert opts["detect_email"] is True
    assert "detect_bogus" not in opts
    assert opts["default_mask_strategy"] == "hash"

    try:
        get_redactor({"pii": {"enabled": True, "mode": "semantic"}})
    except NotImplementedError:
        pass
    else:  # pragma: no cover
        raise AssertionError("semantic mode should raise")

    ent = redact_entity_fields({"content": "x", "trigger": None}, NullRedactor())
    assert ent["content"] == "x"
    print("pii.py ok")
