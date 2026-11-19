import logging
import re
from dataclasses import dataclass
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, Literal

logger = logging.getLogger(__name__)

DEFAULT_TASK_DESCRIPTION = "Task description unknown"


Evidence = Literal["success", "failure", "both"]


GuidelineCategory = Literal["strategy", "recovery", "optimization"]

GUIDELINE_CATEGORIES: tuple[GuidelineCategory, ...] = ("strategy", "recovery", "optimization")

DEFAULT_GUIDELINE_CATEGORY: GuidelineCategory = "strategy"

# Keyed lookup rather than a membership test so a match narrows ``str`` to GuidelineCategory
# for the type checker, and callers can annotate the resolved value as the Literal it is.
_CATEGORY_BY_NAME: dict[str, GuidelineCategory] = {c: c for c in GUIDELINE_CATEGORIES}

# Separators a model reaches for when it names more than one category in a field that takes
# one. The pipe is the common case and comes straight from the prompts, whose output template
# spells the choice as "strategy|recovery|optimization" — a weaker model copies that literally
# instead of picking. The rest are the other spellings of the same mistake.
_CATEGORY_SEPARATOR_RE = re.compile(r"[|,;/+&]|\band\b|\bor\b", re.IGNORECASE)


def resolve_guideline_category(value: object) -> GuidelineCategory | None:
    """Recover one of :data:`GUIDELINE_CATEGORIES` from what a model actually emitted.

    Handles the off-contract shapes whose parts still *name* a real category: a differently-cased
    or padded value, several categories joined by a separator (``"strategy|recovery"``), or a JSON
    array of them — parsed, or still a string (``'["strategy","recovery"]'``). Matching is on whole parts, not substrings, so free text that merely mentions a
    category ("performance optimization work") is not read as one.

    Multi-valued input resolves to the **first** category named — the model's own ordering is the
    only ranking signal available, and the leading entry is the one it reached for first.

    Returns None when nothing recognizable is in there, including for an absent or empty value.
    Repairing only what can be identified is deliberate: a value naming a real category is a
    formatting failure by a model that knew the choices, whereas an invented label means the
    model never picked from them, and quietly filing that under a default would hide it. Callers
    decide what None means for them — see the two in-tree choices, ``Guideline.category`` (hand
    it back to validation, so the response fails as it does today) and consolidation's
    metadata path (fall back to :data:`DEFAULT_GUIDELINE_CATEGORY`, since a stored entity cannot
    be regenerated).
    """
    candidates = value if isinstance(value, (list, tuple)) else [value]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        for part in _CATEGORY_SEPARATOR_RE.split(candidate):
            resolved = _CATEGORY_BY_NAME.get(part.strip().strip("[]\"' ").lower())
            if resolved is not None:
                return resolved
    return None


class Guideline(BaseModel):
    content: str = Field(description="Clear, actionable guideline")
    rationale: str = Field(description="Why this guideline helps")
    category: GuidelineCategory
    trigger: str = Field(description="When to apply this guideline")
    implementation_steps: list[str] = Field(default_factory=list, description="Specific steps to implement this guideline")
    support: int = Field(
        default=1,
        ge=1,
        description="Number of source guidelines merged into this one. Conserved across consolidation (dosage signal).",
    )
    evidence: Evidence | None = Field(
        default=None,
        description="Whether the backing trajectories succeeded, failed, or both. None when unknown.",
    )

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, value: object) -> object:
        """Repair a recoverable category rather than failing the guideline it belongs to.

        Validation runs per response, not per guideline, so the bare ``Literal`` rejected a
        whole generation over one mislabelled entry: ``GuidelineGenerationResponse`` fails,
        ``parse_guideline_response`` returns None, and every guideline mined from that
        trajectory is lost — including the well-formed ones. ``"strategy|recovery"`` from
        gpt-oss is the case in hand, and the prompts invite it by spelling the choice as
        ``"strategy|recovery|optimization"`` in their output template.

        A value naming no known category is handed back untouched, so the ``Literal`` rejects
        it exactly as it does today. Only providers without constrained decoding reach either
        path — with a response schema enforced, the label cannot come back wrong.

        A repair is logged at INFO, so a model or prompt that keeps emitting them stays visible
        instead of being silently rescued.
        """
        resolved = resolve_guideline_category(value)
        if resolved is None or resolved == value:
            return value
        logger.info("Repaired off-contract guideline category %r as %r.", value, resolved)
        return resolved


class _ModelFacingSchema(BaseModel):
    """A response model whose JSON schema, as sent to an LLM, carries no numeric bounds.

    Structured output sends ``model_json_schema()`` to the provider (litellm forwards it
    unchanged), and some providers refuse numeric-range keywords outright: Claude's
    structured outputs reject the whole request with "For 'integer' type, property 'minimum'
    is not supported" (seen through Bedrock). The bounds here (``support``, segment steps)
    are integrity checks, not instructions the model needs, so the schema sent to the model
    omits them and ``model_validate`` still enforces every one locally. Anthropic's SDK
    helpers make the same transformation. See #476.

    Only ``Model.model_json_schema()`` is overridden. That is what ``response_format=Model``
    sends, because litellm's ``type_to_response_format_param`` calls it. These still emit the
    bounds: ``TypeAdapter(Model).json_schema()``,
    ``pydantic.json_schema.model_json_schema(Model)``, a response model nested in a plain
    ``BaseModel``, and ``Guideline`` or ``SubtaskSegment`` sent on their own. Nothing enforces
    the convention, so a model sent to an LLM as a response schema must inherit from this class
    and be passed as the class itself.
    """

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema: dict[str, Any] = _drop_numeric_bounds(super().model_json_schema(*args, **kwargs))
        return schema


_NUMERIC_BOUNDS = frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"})

# Where a JSON Schema node holds sub-schemas. Keywords are dropped only from schema nodes, so a
# name in a map (a field called "minimum"), and data (default, examples, const, enum), stay as
# they are.
_SCHEMA_MAPS = frozenset({"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"})
_SCHEMA_LISTS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SUBSCHEMAS = frozenset(
    {"items", "additionalItems", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames"}
    | {"unevaluatedItems", "unevaluatedProperties"}
)


def _drop_numeric_bounds(schema: Any) -> Any:
    """``schema`` without numeric-bound keywords on any schema node. Anything else, such as a
    boolean schema, is returned as is."""
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _NUMERIC_BOUNDS:
            continue
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: _drop_numeric_bounds(sub) for name, sub in value.items()}
        elif key in _SCHEMA_LISTS and isinstance(value, list):
            out[key] = [_drop_numeric_bounds(sub) for sub in value]
        elif key in _SUBSCHEMAS:
            # items is a list of schemas in draft 4 to 2019-09 tuple form.
            out[key] = [_drop_numeric_bounds(sub) for sub in value] if isinstance(value, list) else _drop_numeric_bounds(value)
        else:
            out[key] = value
    return out


class GuidelineGenerationResponse(_ModelFacingSchema):
    guidelines: list[Guideline]


class ConsolidatedGuideline(Guideline):
    """A consolidated guideline that records which input guidelines it subsumes.

    ``source_indices`` are 0-based indices into the list of input guidelines shown to
    the model. They let consolidation attribute (and conserve) support exactly, rather
    than trusting the model to report counts.
    """

    source_indices: list[int] = Field(
        default_factory=list,
        description="0-based indices of the input guidelines this consolidated guideline merges.",
    )


class ConsolidatedGuidelineResponse(_ModelFacingSchema):
    guidelines: list[ConsolidatedGuideline]


class SubtaskSegment(BaseModel):
    generalized_description: str = Field(
        description="Value-agnostic description of the subtask, applicable to any agent performing a similar operation"
    )
    start_step: int = Field(
        ge=1,
        description=(
            "Inclusive 1-based start index into the filtered reasoning+action steps_list "
            "returned by parse_openai_agents_trajectory — NOT an index into raw messages."
        ),
    )
    end_step: int = Field(
        ge=1,
        description=(
            "Inclusive 1-based end index into the filtered reasoning+action steps_list "
            "returned by parse_openai_agents_trajectory — NOT an index into raw messages."
        ),
    )
    purpose: str = Field(description="What this subtask achieves (phase/output-oriented)")

    @model_validator(mode="after")
    def _check_range(self) -> "SubtaskSegment":
        if self.end_step < self.start_step:
            raise ValueError("end_step must be >= start_step")
        return self


class SegmentationResponse(_ModelFacingSchema):
    subtasks: list[SubtaskSegment] = Field(description="Contiguous, non-overlapping logical subtasks of the trajectory")


@dataclass(frozen=True)
class GuidelineGenerationResult:
    """Internal result from generate_guidelines(), pairing guidelines with the source task description."""

    guidelines: list[Guideline]
    task_description: str


@dataclass(frozen=True)
class ConsolidationResult:
    """Summary of a guideline consolidation run.

    ``support_before``/``support_after`` track the total support (sum of ``support`` over
    the affected guidelines) before and after consolidation. For the current lossless/lossy
    modes they are equal (support is conserved). A future support-threshold filtering step
    (not yet implemented) may reduce ``support_after`` by pruning low-support guidelines.

    ``rounds`` is how many cluster-and-merge rounds ran (0 when consolidation was skipped).
    """

    clusters_found: int
    guidelines_before: int
    guidelines_after: int
    support_before: int = 0
    support_after: int = 0
    rounds: int = 1
