"""Folds outcome observations into the `AggregatedOutcome` summary (Phase 2).

Pure functions. Bucketing rules per `design_doc/transform_evolve.md` §7.1.1:

- `confidence ≥ 0.8`            → confirmed_*
- `0.4 ≤ confidence < 0.8`      → inferred_*
- `signal_source == LLM_JUDGE` → judge_* (regardless of confidence)
- `observed_outcome == UNKNOWN` → unknown counter only (excluded from
                                   confidence_weighted_score)
- `confidence < 0.4`            → dropped (too low to count toward any bucket)

Score formula:
    confidence_weighted_score = Σ(confidence_i × value_i) / Σ(confidence_i)
where `value` is +1 / −1 for success / failure (UNKNOWN observations are
excluded from both numerator and denominator).

Cold-start fallback: when ZERO measured outcomes exist (only `unknown`s or
no observations at all), fall back to the category-based prior from
`compute_cold_start_prior` and remap from probability [0, 1] to score
range [−1, 1] via `2p − 1`.
"""

from __future__ import annotations

from altk_evolve.schema.outcome_evidence import (
    AggregatedOutcome,
    OutcomeEvidence,
    OutcomeKind,
    OutcomeObservation,
    SignalSource,
    compute_cold_start_prior,
)


# Confidence thresholds for bucketing; mirrors §7.1.1.
_CONFIRMED_MIN = 0.8
_INFERRED_MIN = 0.4


def aggregate(
    observations: list[OutcomeObservation],
    *,
    category: str,
    source: str = "llm_extracted",
) -> AggregatedOutcome:
    """Roll a list of OutcomeObservation into an AggregatedOutcome.

    Args:
        observations: append-only ledger of observations for one guideline.
        category: guideline category (strategy/recovery/optimization).
        source: "llm_extracted" (default) or "human_authored". Used only
            for cold-start prior selection when no measured outcomes exist.

    Returns:
        A new AggregatedOutcome. Caller is responsible for replacing the
        previous `OutcomeEvidence.aggregated`.
    """
    out = AggregatedOutcome()

    weighted_sum = 0.0
    weight_sum = 0.0
    last_at = None

    for obs in observations:
        # Track most recent observation regardless of bucket.
        if last_at is None or obs.observed_at > last_at:
            last_at = obs.observed_at

        if obs.observed_outcome is OutcomeKind.UNKNOWN:
            out.unknown += 1
            continue

        # Below confidence floor → drop entirely.
        if obs.confidence < _INFERRED_MIN:
            continue

        # Bucket counters.
        is_success = obs.observed_outcome is OutcomeKind.SUCCESS
        if obs.signal_source is SignalSource.LLM_JUDGE:
            if is_success:
                out.judge_successes += 1
            else:
                out.judge_failures += 1
        elif obs.confidence >= _CONFIRMED_MIN:
            if is_success:
                out.confirmed_successes += 1
            else:
                out.confirmed_failures += 1
        else:  # 0.4 ≤ confidence < 0.8
            if is_success:
                out.inferred_successes += 1
            else:
                out.inferred_failures += 1

        # Weighted score (excludes UNKNOWN, which we already `continue`d on).
        value = 1.0 if is_success else -1.0
        weighted_sum += obs.confidence * value
        weight_sum += obs.confidence

    measured = (
        out.confirmed_successes
        + out.confirmed_failures
        + out.inferred_successes
        + out.inferred_failures
        + out.judge_successes
        + out.judge_failures
    )

    if measured == 0:
        # Cold start: prior is in [0, 1] (probability of success). Remap to score [−1, 1].
        prior = compute_cold_start_prior(category=category, source=source)
        out.confidence_weighted_score = max(-1.0, min(1.0, 2.0 * prior - 1.0))
    else:
        out.confidence_weighted_score = weighted_sum / weight_sum if weight_sum > 0 else 0.0

    out.last_observed_at = last_at
    return out


def update_evidence(
    evidence: OutcomeEvidence,
    new_observations: list[OutcomeObservation],
    *,
    category: str,
    source: str = "llm_extracted",
) -> OutcomeEvidence:
    """Append new observations and recompute the aggregated summary.

    Returns a new OutcomeEvidence (does not mutate the input). The full
    observation list is reaggregated each time — this is fine for the
    moderate volumes expected per guideline (hundreds at most).
    """
    merged = list(evidence.observations) + list(new_observations)
    return OutcomeEvidence(
        observations=merged,
        aggregated=aggregate(merged, category=category, source=source),
    )
