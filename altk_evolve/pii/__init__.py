"""PII redaction for altk-evolve (issue #275).

Strips PII out of entity content before it is persisted to the entity store.
The only backend today is CPEX's regex detector (``cpex-pii-filter``); a
semantic/embedding backend is a documented seam in :func:`get_redactor`.
"""

from altk_evolve.pii.redaction import (
    CpexRegexRedactor,
    NullRedactor,
    PIIRedactor,
    get_redactor,
)

__all__ = [
    "PIIRedactor",
    "NullRedactor",
    "CpexRegexRedactor",
    "get_redactor",
]
