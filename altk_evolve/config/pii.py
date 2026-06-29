from typing import Literal

from pydantic import BaseModel, Field


class PIIConfig(BaseModel):
    """Configuration for PII redaction on entity writes (issue #275).

    Nested under :class:`~altk_evolve.config.evolve.EvolveConfig` as ``pii``.
    With env nesting enabled it is settable via ``EVOLVE_PII__ENABLED=true``,
    ``EVOLVE_PII__MODE=regex``, etc., or programmatically:

        EvolveConfig(pii=PIIConfig(enabled=True))
    """

    enabled: bool = Field(default=False, description="Redact PII from entity content before persisting.")
    mode: Literal["regex", "semantic"] = Field(
        default="regex",
        description="Detection backend. 'regex' uses CPEX (cpex-pii-filter); 'semantic' is a reserved seam.",
    )
    entities: list[str] = Field(
        default_factory=lambda: ["ssn", "credit_card", "email", "phone", "ip_address"],
        description="Which PII entity types to detect (subset of cpex-pii-filter's supported flags).",
    )
    mask_strategy: Literal["redact", "partial", "hash", "tokenize", "remove"] = Field(
        default="redact", description="How matched spans are masked."
    )
    redaction_text: str = Field(default="[REDACTED]", description="Replacement text for the 'redact' strategy.")
    redact_metadata: bool = Field(
        default=False,
        description="Also redact entity metadata values. Off by default — metadata is mostly structural "
        "(trace_id, span_id, timestamps) and prone to false positives.",
    )
