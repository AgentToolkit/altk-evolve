"""PII redaction backends for altk-evolve.

The redactor is the single, pluggable seam for stripping PII out of memory
before it is persisted (issue #275). Today the only backend is the regex
detector from IBM ContextForge's ``cpex-pii-filter`` plugin
(``PIIDetectorRust``). A semantic / embedding backend is a documented seam in
:func:`get_redactor` — CPEX ships no semantic detector, so that mode would come
from a non-CPEX library (e.g. Presidio + spaCy/transformers).

Design goals:

- **Zero hard dependency.** ``cpex-pii-filter`` is imported lazily, so importing
  this module never fails. A deployment that has not installed the ``[pii]``
  extra gets a no-op redactor (with a loud warning when redaction was explicitly
  enabled) rather than a broken write path.
- **Config-gated and opt-in.** With ``enabled: false`` (the default) the factory
  returns a :class:`NullRedactor`, so existing flows are untouched.
- **Structure-aware.** :meth:`PIIRedactor.redact_value` walks ``str | list |
  dict`` content (an entity's ``content`` may be any of these) and masks string
  leaves, leaving structure intact.
"""

from __future__ import annotations

import logging
from typing import cast

logger = logging.getLogger(__name__)


# Entity flags supported by cpex-pii-filter's regex detector (PIIDetectorRust),
# mapped to its ``detect_<name>`` boolean constructor options.
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

# Sensible default set when the config does not pin an explicit entity list.
DEFAULT_ENTITIES = ("ssn", "credit_card", "email", "phone", "ip_address")

DEFAULT_MASK_STRATEGY = "redact"
DEFAULT_REDACTION_TEXT = "[REDACTED]"


def _cfg(pii_config, key, default=None):
    """Read *key* from a PIIConfig-like object or a plain dict (duck-typed).

    Keeps this module decoupled from ``altk_evolve.config.pii`` (no import
    cycle): callers may pass a PIIConfig pydantic instance or a dict.
    """
    if pii_config is None:
        return default
    if isinstance(pii_config, dict):
        return pii_config.get(key, default)
    return getattr(pii_config, key, default)


class PIIRedactor:
    """Interface: turn text into text with PII removed/masked."""

    #: When True, the write choke-point also redacts entity metadata values.
    #: Set by :func:`get_redactor` from ``PIIConfig.redact_metadata``.
    redact_metadata: bool = False

    def redact(self, text: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def detect(self, text: str) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    def redact_value(self, value):
        """Recursively redact string leaves of a ``str | list | dict`` value.

        Non-string scalars (ints, bools, None) are returned unchanged so
        structural data is preserved.
        """
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_value(v) for v in value]
        if isinstance(value, dict):
            return {k: self.redact_value(v) for k, v in value.items()}
        return value


class NullRedactor(PIIRedactor):
    """No-op redactor: returns input unchanged.

    Used when PII redaction is disabled or the backend is unavailable so the
    write path keeps working.
    """

    def redact(self, text: str) -> str:
        return text

    def detect(self, text: str) -> dict:
        return {}

    def redact_value(self, value):
        return value


def detector_options(pii_config) -> dict:
    """Map a PIIConfig-like object/dict to ``PIIDetectorRust`` options.

    Pure function (no CPEX import) so it is trivially testable. Unknown entity
    names are dropped, since ``PIIDetectorRust`` only understands the
    ``detect_<name>`` flags in :data:`SUPPORTED_ENTITIES`.
    """
    entities = _cfg(pii_config, "entities") or list(DEFAULT_ENTITIES)
    if isinstance(entities, str):
        entities = [e.strip() for e in entities.split(",") if e.strip()]
    opts: dict = {f"detect_{e}": True for e in entities if e in SUPPORTED_ENTITIES}
    opts["default_mask_strategy"] = _cfg(pii_config, "mask_strategy", DEFAULT_MASK_STRATEGY)
    opts["redaction_text"] = _cfg(pii_config, "redaction_text", DEFAULT_REDACTION_TEXT)
    custom = _cfg(pii_config, "custom_patterns")
    if custom:
        opts["custom_patterns"] = custom
    whitelist = _cfg(pii_config, "whitelist_patterns")
    if whitelist:
        opts["whitelist_patterns"] = whitelist
    return opts


class CpexRegexRedactor(PIIRedactor):
    """Regex-based redactor backed by ``cpex-pii-filter``'s ``PIIDetectorRust``.

    The CPEX detector is a self-contained Rust regex engine: it runs standalone
    (no enforcement gateway / reference monitor) and pulls no ML dependencies.
    It has no NER, so it catches structured PII (emails, phones, SSNs, cards,
    IPs, …) but not free-form names — see :data:`SUPPORTED_ENTITIES`.
    """

    def __init__(self, pii_config=None):
        from cpex_pii_filter import PIIDetectorRust  # lazy import: optional [pii] extra

        self._detector = PIIDetectorRust(detector_options(pii_config))

    def detect(self, text: str) -> dict:
        if not text:
            return {}
        return cast(dict, self._detector.detect(text))

    def redact(self, text: str) -> str:
        if not text:
            return text
        return cast(str, self._detector.mask(text, self._detector.detect(text)))


def get_redactor(pii_config) -> PIIRedactor:
    """Return a :class:`PIIRedactor` for the given PII config.

    *pii_config* may be a ``PIIConfig`` instance or a plain dict.

    - PII disabled (or no config) -> :class:`NullRedactor` (no-op).
    - ``mode == "regex"`` (default) -> :class:`CpexRegexRedactor`, or a
      :class:`NullRedactor` with a loud warning when ``cpex-pii-filter`` is not
      installed (a misconfiguration, not a crash).
    - ``mode == "semantic"`` -> :class:`NotImplementedError`. This is the seam
      for a future embedding/NER backend. CPEX has no semantic detector today;
      add a ``SemanticRedactor`` (e.g. Presidio + spaCy/transformers) and
      dispatch to it here.
    """
    if not _cfg(pii_config, "enabled", False):
        return NullRedactor()

    redact_metadata = bool(_cfg(pii_config, "redact_metadata", False))
    mode = (_cfg(pii_config, "mode", "regex") or "regex").strip().lower()
    if mode == "regex":
        try:
            redactor: PIIRedactor = CpexRegexRedactor(pii_config)
        except ImportError:
            logger.warning(
                "pii.enabled is set but 'cpex-pii-filter' is not installed; "
                "PII will NOT be redacted. Install the project's [pii] extra "
                "(pip install 'altk-evolve[pii]') or `pip install cpex-pii-filter`."
            )
            return NullRedactor()
        redactor.redact_metadata = redact_metadata
        return redactor
    if mode == "semantic":
        raise NotImplementedError(
            "pii.mode 'semantic' is not implemented. CPEX ships only a regex "
            "detector (cpex-pii-filter / PIIDetectorRust). A semantic/embedding "
            "backend is the documented seam here — add a SemanticRedactor "
            "(e.g. Presidio + spaCy/transformers) and dispatch to it in get_redactor()."
        )
    raise ValueError(f"Unknown pii.mode: {mode!r} (expected 'regex' or 'semantic')")
