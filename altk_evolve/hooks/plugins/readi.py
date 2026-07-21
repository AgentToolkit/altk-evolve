"""Semantic (NER) PII redaction plugin, backed by IBM READI (``readi-privacy``).

Why this exists alongside :mod:`altk_evolve.hooks.plugins.pii`: the shipped
regex redactor (cpex-pii-filter) is a Rust regex engine with no NER. It catches
structured identifiers (email, SSN, phone, card, IP) at precision ~1.00, but it
cannot catch a *name*. Measured on the ai4privacy corpus with
``examples/pii_benchmark.py`` (200 rows of ai4privacy/pii-masking-200k): regex
overall span recall **0.13** vs READI semantic **0.48**, both at precision
1.00, with first/last name going **0.00 -> 0.92/0.94**. On Japanese
(``ai4privacy/pii-masking-openpii-1.5m``, 2,000 rows) a language-matched spaCy
pipeline reaches **0.92** recall / ~0.99 precision against **0.03** for regex.
Semantic redaction is the difference between "redacts identifiers" and
"redacts people".

Core/shim split (see ``docs/guides/memory-hooks.md``):

* **Core** — :func:`redact_spans`, :func:`redact_entities`,
  :func:`redact_messages` and the :class:`SpanDetector` protocol at module top.
  Pure, engine-free, no cpex and no READI import: detection is *injected*, so
  the splice logic is unit-testable with a fake detector and no extras
  installed. :func:`build_readi_detector` is the (lazy, READI-importing)
  factory that produces a real detector.
* **Shim** — :class:`ReadiSemanticPIIPlugin` under the ``HAS_CPEX`` guard:
  parses ``self._config.config``, calls the core, returns ``PluginResult``
  with a ``modified_payload``.

Offsets are **character** offsets throughout. READI's extractors report char
offsets natively (unlike cpex-pii-filter, whose Rust engine reports *byte*
offsets — converting those was what fixed Japanese precision 0.31 -> 0.99 in
the PoC). The splice here therefore indexes ``str`` directly, and
``tests/unit/test_readi_redaction_core.py`` pins that with a multibyte
regression test: a byte-offset detector would mis-splice Japanese text.

Requires ``pip install 'altk-evolve[pii-semantic]'`` (the semantic PII method;
the regex method is ``[pii-regex]``, and running both is the recommended
defence-in-depth default).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from typing import Any, Protocol, runtime_checkable

from altk_evolve.hooks.types import HAS_CPEX, HookType

DEFAULT_REDACTION_TEXT = "[REDACTED]"
DEFAULT_EXTRACTOR = "default"
DEFAULT_SPACY_MODEL = "en_core_web_trf"
#: Extractor names accepted by :func:`build_readi_detector`.
EXTRACTORS = ("default", "spacy", "hf", "presidio")


@runtime_checkable
class SpanDetector(Protocol):
    """Anything that maps text to ``(start, end)`` **character** spans.

    Deliberately narrower than READI's ``Entity`` (which also carries
    ``entity_type``): the redaction core only needs offsets, so the core stays
    testable with a two-line fake and independent of READI's object model.
    """

    def __call__(self, text: str) -> Iterable[tuple[int, int]]: ...


def redact_spans(text: str, spans: Iterable[tuple[int, int]], *, mask: str = DEFAULT_REDACTION_TEXT) -> str:
    """Splice ``mask`` over each ``(start, end)`` character span of ``text``.

    Pure. Spans are applied right-to-left so earlier offsets stay valid as the
    string is rewritten, and overlapping/adjacent spans are merged first so a
    detector that reports both ``PERSON`` and ``NAME`` over the same words
    yields one ``[REDACTED]``, not two.

    ``start``/``end`` are character offsets into ``text`` (see module docstring
    on why that matters for multibyte scripts). Out-of-range and inverted spans
    are clamped/dropped rather than raising — a detector is untrusted input.
    """
    if not text:
        return text
    # Clamp FIRST, then drop zero-width. Filtering `e > s` before clamping lets a
    # fully-out-of-range span (e.g. (10, 20) on a 6-char string) clamp to (6, 6)
    # and splice as a spurious INSERTION of `mask`; dropping zero-width spans
    # *after* clamping keeps such spans a genuine no-op (None-unchanged contract).
    clamped = sorted((cs, ce) for cs, ce in ((max(0, min(int(s), len(text))), max(0, min(int(e), len(text)))) for s, e in spans) if cs < ce)
    if not clamped:
        return text
    merged: list[tuple[int, int]] = []
    for start, end in clamped:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    out = text
    for start, end in reversed(merged):
        out = out[:start] + mask + out[end:]
    return out


def redact_text(text: str, detect: SpanDetector, *, mask: str = DEFAULT_REDACTION_TEXT) -> str:
    """Detect spans in ``text`` with ``detect`` and mask them. Pure given ``detect``."""
    if not text:
        return text
    return redact_spans(text, detect(text), mask=mask)


#: Message / tool_call keys that carry identity or routing, not free text. When
#: walking a whole chat message (see :func:`redact_messages`) these are passed
#: through verbatim at every nesting depth so a detector false-positive can never
#: rewrite a ``role``, an id, a tool-call ``type`` (``"function"``), or a
#: function ``name`` — only the text-bearing leaves (``content``, and
#: ``tool_calls[].function.arguments``) are redacted.
MESSAGE_STRUCTURAL_KEYS = frozenset({"role", "id", "type", "name", "tool_call_id"})


def _redact_value(value: Any, detect: SpanDetector, *, mask: str, skip: frozenset[str] = frozenset()) -> Any:
    """Recursively redact string leaves of a ``str | list | dict`` value.

    An entity's ``content`` may be any of these; non-string scalars (ints,
    bools, None) pass through so structure survives redaction. Keys in ``skip``
    are copied through untouched at any depth (used to protect structural
    message fields like ``role``/ids from redaction); ``skip`` is empty by
    default, so entity/metadata redaction is unaffected.
    """
    if isinstance(value, str):
        return redact_text(value, detect, mask=mask)
    if isinstance(value, list):
        return [_redact_value(v, detect, mask=mask, skip=skip) for v in value]
    if isinstance(value, dict):
        return {k: (v if k in skip else _redact_value(v, detect, mask=mask, skip=skip)) for k, v in value.items()}
    return value


def redact_entities(
    entities: Sequence[dict],
    detect: SpanDetector,
    *,
    mask: str = DEFAULT_REDACTION_TEXT,
    redact_metadata: bool = True,
) -> list[dict] | None:
    """Return redacted copies of ``entities``, or ``None`` when nothing changed.

    ``content`` is always redacted; ``metadata`` values when ``redact_metadata``
    is set — **default True, to match the shipped regex plugin**, which round-
    trips the whole entity through cpex-pii-filter and so redacts metadata
    unconditionally. Shipping the two PII plugins with opposite metadata defaults
    would be a silent parity gap, and the fail-safe default for a redactor is to
    mask more. It is an opt-*out* (``redact_metadata=False``): metadata can hold
    ids/paths/trace keys that redaction would corrupt, so a deployment that keys
    on those can turn it off deliberately.

    Pure: input dicts are never mutated — the seam's plugin contract requires
    changes to travel back as a replacement payload, never in-place.
    """
    changed = False
    out: list[dict] = []
    for entity in entities:
        updated = dict(entity)
        content = _redact_value(entity.get("content"), detect, mask=mask)
        if content != entity.get("content"):
            updated["content"] = content
            changed = True
        if redact_metadata and entity.get("metadata"):
            metadata = _redact_value(entity["metadata"], detect, mask=mask)
            if metadata != entity["metadata"]:
                updated["metadata"] = metadata
                changed = True
        out.append(updated)
    return out if changed else None


def redact_messages(
    messages: Sequence[dict],
    detect: SpanDetector,
    *,
    mask: str = DEFAULT_REDACTION_TEXT,
) -> list[dict] | None:
    """Return redacted copies of chat ``messages``, or ``None`` when unchanged.

    Redacts ALL text-bearing parts of each message, not just ``content``:
    critically ``tool_calls[].function.arguments`` (typically a JSON string of
    the model's tool inputs), which otherwise re-sends unredacted PII to the LLM
    on every turn — a real egress leak, and a parity gap versus the regex plugin
    which redacts the whole payload. The message is walked with
    :data:`MESSAGE_STRUCTURAL_KEYS` skipped, so ids/roles/types/function-names
    are preserved while every free-text leaf is masked. The tool-call
    ``arguments`` string is redacted as raw text, which is safe because
    :func:`redact_text` only rewrites detector-flagged substrings (the PII
    *values*), leaving the surrounding JSON intact.

    Pure; inputs are never mutated (``_redact_value`` rebuilds dicts/lists).
    """
    changed = False
    out: list[dict] = []
    for message in messages:
        redacted = _redact_value(message, detect, mask=mask, skip=MESSAGE_STRUCTURAL_KEYS)
        out.append(redacted)
        if redacted != message:
            changed = True
    return out if changed else None


def build_readi_detector(
    *,
    extractor: str = DEFAULT_EXTRACTOR,
    model: str | None = None,
    language: str = "en",
    detection_type: str = "PII",
) -> SpanDetector:
    """Build a :class:`SpanDetector` backed by IBM READI. Imports ``risk_assessment``.

    The engine is selected from the extractors READI already ships — no custom
    NER pipeline is assembled here:

    - ``default``: READI's own ``DetectionType.PII`` pipeline (spaCy
      ``en_core_web_trf`` + READI's identifier extractors). English only.
    - ``spacy`` + ``model``: ``SpacyEntityExtractor`` over any spaCy pipeline —
      this is the multilingual path (``ja_core_news_trf``, ``de_core_news_lg``,
      ...). NER entities only, no structured identifiers.
    - ``hf`` + ``model``: ``HFEntityExtractor`` over any ``pipeline("ner")``
      model id.
    - ``presidio``: ``PresidioEntityExtractor`` (Microsoft Presidio; NER plus
      structured recognizers). See the ``language`` caveat below.

    Model loading is deferred to the first call — construction only validates
    that READI is importable, so a shim built at startup does not pay (or fail
    on) a multi-hundred-MB weight download.

    Limitation: READI's Presidio wrapper hardcodes ``language="en"`` internally
    (flagged as needing a fix upstream), so ``language`` reaches the spaCy
    engine configuration but not Presidio's own analyze call — multilingual
    Presidio needs an upstream fix or a local override. Prefer
    ``extractor="spacy"`` with a language-matched model for non-English.
    """
    if extractor not in EXTRACTORS:
        raise ValueError(f"Unknown readi extractor {extractor!r} (expected one of {', '.join(EXTRACTORS)})")

    # Cheap import; validates the [pii-semantic] extra without loading model weights.
    # Narrow guard: only a genuinely-absent READI package gets the install hint.
    # A name-less ImportError, or one naming an unrelated module, means a broken
    # install rather than a missing extra and must surface as-is — masking it
    # would silently disable a compliance plugin.
    try:
        from risk_assessment.readi.analyzer import READIAnalyzer
    except ModuleNotFoundError as exc:
        if exc.name == "risk_assessment" or (exc.name or "").startswith("risk_assessment."):
            raise ImportError(
                "Semantic PII redaction requires IBM READI. Install it with: pip install 'altk-evolve[pii-semantic]'"
            ) from exc
        raise

    def _build_extractor() -> Any:
        if extractor == "spacy":
            from risk_assessment.classification.unstructured.spacy import SpacyEntityExtractor

            return SpacyEntityExtractor(model or DEFAULT_SPACY_MODEL)
        if extractor == "hf":
            if not model:
                raise ValueError("readi_model is required when readi_extractor='hf'")
            from risk_assessment.classification.unstructured.hf import HFEntityExtractor

            return HFEntityExtractor(model)
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        from risk_assessment.classification.unstructured.presidio import PresidioEntityExtractor

        nlp_engine = NlpEngineProvider(
            nlp_configuration={"nlp_engine_name": "spacy", "models": [{"lang_code": language, "model_name": model or DEFAULT_SPACY_MODEL}]}
        ).create_engine()
        return PresidioEntityExtractor({}, nlp_engine=nlp_engine, supported_languages=[language])

    def _build_analyzer() -> Any:
        if extractor == "default":
            kind = getattr(READIAnalyzer.DetectionType, detection_type.upper(), READIAnalyzer.DetectionType.PII)
            return READIAnalyzer(detection_type=kind)
        from risk_assessment.classification.unstructured.aggregator import AggregatorConfiguration

        return READIAnalyzer(
            READIAnalyzer.DetectionType.CUSTOM,
            entity_extractors=[_build_extractor()],
            aggregator_configuration=AggregatorConfiguration(merge_entities=True),
        )

    # One-slot lazy cache: models load on first detect, not at construction.
    # The lock only guards *construction* — the hook seam's sync bridge runs
    # dispatch on a dedicated thread whenever an event loop is already running,
    # and two threads racing here would each download/load a multi-hundred-MB
    # pipeline.
    #
    # Apple Silicon caveat: spacy-curated-transformers places these models on
    # torch's MPS backend, and MPS binds to the first thread that touches it —
    # a model used from a second thread raises "Placeholder storage has not
    # been allocated on MPS device!" no matter where it was built (a per-thread
    # cache does NOT help; verified). With on_error=fail that surfaces as a
    # blocked operation. See the "Known limitations" section of
    # docs/guides/pii-redaction.md for workarounds; it does not affect
    # CPU/CUDA hosts.
    cache: list[Any] = []
    lock = threading.Lock()

    def detect(text: str) -> list[tuple[int, int]]:
        if not cache:
            with lock:
                if not cache:
                    cache.append(_build_analyzer())
        # READI reports character offsets, so these index `str` directly.
        return [(e.start, e.end) for e in cache[0].detect(text)]

    return detect


if HAS_CPEX:
    from cpex.framework import Plugin
    from cpex.framework.models import OnError, PluginConfig, PluginMode, PluginResult

    def _default_config() -> PluginConfig:
        return PluginConfig(
            name="readi_semantic_pii",
            kind="altk_evolve.hooks.plugins.readi.ReadiSemanticPIIPlugin",
            hooks=[HookType.MEMORY_PRE_WRITE.value, HookType.LLM_PRE_CALL.value],
            # SEQUENTIAL, not TRANSFORM — same reason as the regex plugin: CPEX
            # silently downgrades continue_processing=False -> True in
            # TRANSFORM/AUDIT modes, so a redactor registered there can redact
            # but can NEVER block. Only sequential keeps both.
            mode=PluginMode.SEQUENTIAL,
            # Same slot as the regex filter: redact before the normalizer runs.
            priority=10,
            # Fail-closed: a crashing or timing-out NER model must halt the
            # operation, never silently pass unredacted content through.
            on_error=OnError.FAIL,
            config={"redaction_text": DEFAULT_REDACTION_TEXT},
        )

    class ReadiSemanticPIIPlugin(Plugin):
        """Thin cpex shim: READI semantic redaction on writes and LLM egress.

        Config keys (all optional):
          - ``readi_extractor``: ``default`` | ``spacy`` | ``hf`` | ``presidio``
          - ``readi_model``: spaCy pipeline name or HF ``pipeline("ner")`` id
          - ``readi_language``: language code for the spacy/presidio engine
          - ``readi_detection_type``: READI ``DetectionType`` for ``default``
          - ``redaction_text``: mask string (default ``[REDACTED]``)
          - ``redact_metadata``: also redact entity metadata values (default True,
            matching the regex plugin; set False to preserve ids/paths in metadata)
        """

        def __init__(self, config: PluginConfig | None = None) -> None:
            super().__init__(config or _default_config())
            self._detector: SpanDetector | None = None

        @property
        def _cfg(self) -> dict:
            return self._config.config or {}

        def startup_validate(self) -> None:
            """Build the detector at engine init so a missing ``[pii-semantic]``
            extra fails CLOSED here (with the extra-naming ImportError) rather
            than lazily on the first write. Model weights still load on first
            use — ``build_readi_detector`` only validates that READI imports,
            it does not download weights."""
            self._detect()

        def _detect(self) -> SpanDetector:
            """Build the READI detector once, on first use (weights load lazily)."""
            if self._detector is None:
                cfg = self._cfg
                self._detector = build_readi_detector(
                    extractor=str(cfg.get("readi_extractor") or DEFAULT_EXTRACTOR).strip().lower(),
                    model=cfg.get("readi_model"),
                    language=str(cfg.get("readi_language") or "en"),
                    detection_type=str(cfg.get("readi_detection_type") or "PII"),
                )
            return self._detector

        def _result(self, payload: Any, field: str, value: list[dict] | None) -> Any:
            if value is None:
                return PluginResult(continue_processing=True)
            # Contract: changes travel back as a replacement payload; the input
            # payload is never mutated in place.
            return PluginResult(continue_processing=True, modified_payload=payload.model_copy(update={field: value}))

        async def memory_pre_write(self, payload: Any, context: Any) -> Any:
            cfg = self._cfg
            return self._result(
                payload,
                "entities",
                redact_entities(
                    payload.entities,
                    self._detect(),
                    mask=cfg.get("redaction_text", DEFAULT_REDACTION_TEXT),
                    redact_metadata=bool(cfg.get("redact_metadata", True)),
                ),
            )

        async def llm_pre_call(self, payload: Any, context: Any) -> Any:
            cfg = self._cfg
            return self._result(
                payload,
                "messages",
                redact_messages(payload.messages, self._detect(), mask=cfg.get("redaction_text", DEFAULT_REDACTION_TEXT)),
            )

else:

    class ReadiSemanticPIIPlugin:  # type: ignore[no-redef]
        """Stub — install 'altk-evolve[pii-semantic]' for semantic PII redaction support."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "ReadiSemanticPIIPlugin requires the CPEX plugin framework and IBM READI. "
                "Install them with: pip install 'altk-evolve[pii-semantic]'"
            )


__all__ = [
    "EXTRACTORS",
    "ReadiSemanticPIIPlugin",
    "SpanDetector",
    "build_readi_detector",
    "redact_entities",
    "redact_messages",
    "redact_spans",
    "redact_text",
]
