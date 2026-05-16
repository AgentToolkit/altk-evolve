"""Outcome-evidence schema for guideline track.

Captures provenance-aware, confidence-weighted observations of guideline
relevance from real trajectories. Six signal sources are supported, ranging
from explicit user feedback (high confidence, low coverage) to implicit
usage signals (medium confidence, full coverage).

Design rationale lives in design_doc/transform_evolve.md §7.1.1 and the
Phase 0 deliverables of design_doc/implementation_plan.md.

Backwards-compat: this is a new optional field on `Guideline`. Existing
records without `outcome_evidence` load as `None`.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SignalSource(str, Enum):
    """Provenance of an outcome observation.

    Ordering reflects confidence × coverage × cost trade-offs (§7.1.1):
    - EXPLICIT_FEEDBACK: very high confidence, low coverage (~5–15%), free
    - TOOL_ERROR: high confidence, medium-high coverage, free
    - TRAJECTORY_SHAPE: medium confidence, high coverage, free
    - REPLY_PATTERN: medium confidence, medium coverage, cheap LLM
    - LLM_JUDGE: medium-low confidence, 100% coverage, expensive
    - IMPLICIT_USAGE: medium confidence, 100% coverage, free byproduct of telemetry
    """

    EXPLICIT_FEEDBACK = "explicit_feedback"
    TOOL_ERROR = "tool_error"
    TRAJECTORY_SHAPE = "trajectory_shape"
    REPLY_PATTERN = "reply_pattern"
    LLM_JUDGE = "llm_judge"
    IMPLICIT_USAGE = "implicit_usage"


class OutcomeKind(str, Enum):
    """Three-valued outcome state.

    `unknown` is first-class — a trajectory observed without a usable signal
    is counted for frequency but never moves the success/failure scoreboard.
    Distinguishing `unknown=N, failure=0` (unmeasured) from `unknown=0, failure=N`
    (consistently bad rule) is load-bearing for retrieval ranking.
    """

    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


class OutcomeObservation(BaseModel):
    """Single outcome observation from one trajectory."""

    trajectory_id: str = Field(description="ID of the source trajectory.")
    signal_source: SignalSource = Field(description="Where this observation came from.")
    observed_outcome: OutcomeKind = Field(description="success | failure | unknown.")
    confidence: float = Field(ge=0.0, le=1.0, description="0.0–1.0 confidence in the observation.")
    observed_at: datetime = Field(description="UTC timestamp when the observation was recorded.")
    detail: str | None = Field(default=None, description="Free-form note (e.g. 'auth_handler raised 401').")


class AggregatedOutcome(BaseModel):
    """Roll-up of observations into bucketed counters + a single weighted score.

    Bucketing rule (§7.1.1):
    - confidence ≥ 0.8 → confirmed_*
    - 0.4 ≤ confidence < 0.8 → inferred_*
    - signal_source == LLM_JUDGE → judge_* (regardless of confidence)
    - observed_outcome == UNKNOWN → unknown counter only
    """

    confirmed_successes: int = Field(default=0, ge=0)
    confirmed_failures: int = Field(default=0, ge=0)
    inferred_successes: int = Field(default=0, ge=0)
    inferred_failures: int = Field(default=0, ge=0)
    judge_successes: int = Field(default=0, ge=0)
    judge_failures: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0, description="Observations with no usable signal.")
    confidence_weighted_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Σ(confidence_i × outcome_value_i) / Σ(confidence_i), where outcome_value is "
            "1.0 / 0.0 for success / failure (UNKNOWN excluded). Range [0, 1] reads as "
            "probability-of-success: 0.0 = all failures, 0.5 = no data / neutral, 1.0 = "
            "all successes. Falls back to category prior when no observations exist."
        ),
    )
    last_observed_at: datetime | None = Field(default=None)


class OutcomeEvidence(BaseModel):
    """Per-guideline outcome evidence ledger.

    Layered storage:
    - `observations` keeps the full provenance trail (one record per trajectory event).
    - `aggregated` is a denormalized roll-up updated by the daily aggregation job
      so retrieval can rank without re-aggregating on every read.

    Conflict resolution between two contradictory guidelines uses
    `aggregated.confidence_weighted_score`. Tied scores or all-unknown
    pairs route to human review (see `llm/conflict_resolution/guideline_resolver.py`).

    Cold start: a new guideline with zero observations gets a category-based
    prior from `compute_cold_start_prior()`; the prior is overwritten as
    real observations accumulate.
    """

    observations: list[OutcomeObservation] = Field(default_factory=list)
    aggregated: AggregatedOutcome = Field(default_factory=AggregatedOutcome)

    @field_validator("observations")
    @classmethod
    def _bounded_observations(cls, v: list[OutcomeObservation]) -> list[OutcomeObservation]:
        # Keep the unbounded growth in check; trim and re-aggregate is a future Phase 2 concern.
        # Phase 0 just enforces a soft ceiling that is unlikely to be hit in tests.
        if len(v) > 10_000:
            raise ValueError(f"observations cap exceeded: {len(v)} > 10000; trim before persisting")
        return v


# Cold-start priors per category (§7.1.1; tunable in Phase 0 sweep).
# These set initial `confidence_weighted_score` for guidelines with zero observations,
# so brand-new guidelines do not always rank below older peers.
COLD_START_PRIOR_BY_CATEGORY: dict[str, float] = {
    "strategy": 0.6,  # someone wrote it deliberately
    "recovery": 0.5,  # we tried it because something failed
    "optimization": 0.55,
}

# Default prior for LLM-extracted guidelines (lower than human-authored).
COLD_START_PRIOR_LLM_EXTRACTED: float = 0.4


def compute_cold_start_prior(*, category: str, source: str = "llm_extracted") -> float:
    """Return the initial score for a guideline with no observations.

    Args:
        category: Guideline category (strategy/recovery/optimization).
        source: "llm_extracted" (default; uses the LLM-extracted prior)
                or "human_authored" (uses the per-category prior).
    """
    if source == "human_authored":
        return COLD_START_PRIOR_BY_CATEGORY.get(category, COLD_START_PRIOR_LLM_EXTRACTED)
    return COLD_START_PRIOR_LLM_EXTRACTED
