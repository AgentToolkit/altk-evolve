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
        description="Detection backend. 'regex' uses CPEX (cpex-pii-filter); 'semantic' uses IBM READI (readi-privacy) NER.",
    )
    readi_detection_type: Literal["PII", "PHI", "PII_NO_MODEL"] = Field(
        default="PII",
        description="READI DetectionType for the default extractor. 'PII_NO_MODEL' skips the transformer (faster, less recall).",
    )
    readi_extractor: Literal["default", "spacy", "hf", "presidio"] = Field(
        default="default",
        description="Which READI-provided extractor mode=semantic uses. 'default' = READI's spaCy-English PII pipeline; "
        "'spacy'/'hf' swap the NER model (needs readi_model); 'presidio' uses Microsoft Presidio.",
    )
    readi_model: str | None = Field(
        default=None,
        description="Model for the chosen extractor: a spaCy pipeline name (spacy/presidio, e.g. 'ja_core_news_trf') "
        "or a Hugging Face pipeline('ner') id (hf). None uses the extractor's default (en_core_web_trf).",
    )
    readi_language: str = Field(
        default="en",
        description="Language code for the spacy/presidio extractors (e.g. 'ja').",
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
    custom_patterns: list[dict] = Field(
        default_factory=list,
        description="Extra user regex patterns for the detector; each is {name, description, pattern}. "
        "Use to catch entities the built-in flags miss (e.g. specific names — the regex backend has no NER).",
    )
    whitelist_patterns: list[dict] = Field(
        default_factory=list,
        description="Patterns to exempt from redaction (passed through to the detector).",
    )
