"""Tests for altk_evolve.llm.outcome_extraction.aggregator (Phase 2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from altk_evolve.llm.outcome_extraction.aggregator import aggregate, update_evidence
from altk_evolve.schema.outcome_evidence import (
    COLD_START_PRIOR_BY_CATEGORY,
    COLD_START_PRIOR_LLM_EXTRACTED,
    OutcomeEvidence,
    OutcomeKind,
    OutcomeObservation,
    SignalSource,
)


pytestmark = pytest.mark.unit


def _ts(offset_seconds: int = 0) -> datetime:
    return datetime(2026, 5, 15, 14, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)


def _obs(
    *,
    outcome: OutcomeKind = OutcomeKind.SUCCESS,
    confidence: float = 0.9,
    source: SignalSource = SignalSource.TOOL_ERROR,
    when: int = 0,
    traj: str = "traj-1",
) -> OutcomeObservation:
    return OutcomeObservation(
        trajectory_id=traj,
        signal_source=source,
        observed_outcome=outcome,
        confidence=confidence,
        observed_at=_ts(when),
    )


# ── bucketing rules ───────────────────────────────────────────────────────


class TestBucketing:
    def test_confidence_ge_0_8_lands_in_confirmed(self) -> None:
        agg = aggregate([_obs(confidence=0.9), _obs(confidence=0.8)], category="strategy")
        assert agg.confirmed_successes == 2
        assert agg.inferred_successes == 0

    def test_confidence_in_0_4_to_0_8_lands_in_inferred(self) -> None:
        agg = aggregate([_obs(confidence=0.5), _obs(confidence=0.79)], category="strategy")
        assert agg.inferred_successes == 2
        assert agg.confirmed_successes == 0

    def test_confidence_below_0_4_is_dropped(self) -> None:
        agg = aggregate([_obs(confidence=0.39), _obs(confidence=0.0)], category="strategy")
        assert agg.confirmed_successes == 0
        assert agg.inferred_successes == 0
        assert agg.unknown == 0  # not counted as unknown either

    def test_llm_judge_routes_to_judge_bucket_regardless_of_confidence(self) -> None:
        agg = aggregate(
            [
                _obs(source=SignalSource.LLM_JUDGE, confidence=0.5, outcome=OutcomeKind.SUCCESS),
                _obs(source=SignalSource.LLM_JUDGE, confidence=0.95, outcome=OutcomeKind.FAILURE),
            ],
            category="strategy",
        )
        assert agg.judge_successes == 1
        assert agg.judge_failures == 1
        assert agg.confirmed_successes == 0
        assert agg.inferred_successes == 0

    def test_failures_route_to_failure_buckets(self) -> None:
        agg = aggregate(
            [
                _obs(outcome=OutcomeKind.FAILURE, confidence=0.95),
                _obs(outcome=OutcomeKind.FAILURE, confidence=0.5),
            ],
            category="recovery",
        )
        assert agg.confirmed_failures == 1
        assert agg.inferred_failures == 1

    def test_unknown_increments_unknown_only(self) -> None:
        agg = aggregate(
            [
                _obs(outcome=OutcomeKind.UNKNOWN, confidence=0.0),
                _obs(outcome=OutcomeKind.UNKNOWN, confidence=0.9),  # confidence ignored for UNKNOWN
            ],
            category="strategy",
        )
        assert agg.unknown == 2
        assert agg.confirmed_successes == 0
        assert agg.inferred_successes == 0


# ── confidence-weighted score ─────────────────────────────────────────────


class TestScore:
    def test_pure_successes_score_positive(self) -> None:
        agg = aggregate(
            [_obs(confidence=0.9, outcome=OutcomeKind.SUCCESS) for _ in range(3)],
            category="strategy",
        )
        assert agg.confidence_weighted_score == pytest.approx(1.0)

    def test_pure_failures_score_negative(self) -> None:
        agg = aggregate(
            [_obs(confidence=0.9, outcome=OutcomeKind.FAILURE) for _ in range(3)],
            category="strategy",
        )
        assert agg.confidence_weighted_score == pytest.approx(-1.0)

    def test_mixed_outcomes_weighted_by_confidence(self) -> None:
        # 1 success at 0.9 + 1 failure at 0.5 → (0.9 - 0.5) / (0.9 + 0.5) = 0.4 / 1.4
        agg = aggregate(
            [
                _obs(confidence=0.9, outcome=OutcomeKind.SUCCESS),
                _obs(confidence=0.5, outcome=OutcomeKind.FAILURE),
            ],
            category="strategy",
        )
        assert agg.confidence_weighted_score == pytest.approx(0.4 / 1.4)

    def test_unknown_excluded_from_score(self) -> None:
        # 1 success + 99 unknowns → score should still be 1.0
        observations = [_obs(confidence=0.9, outcome=OutcomeKind.SUCCESS)]
        observations.extend(_obs(confidence=0.5, outcome=OutcomeKind.UNKNOWN) for _ in range(99))
        agg = aggregate(observations, category="strategy")
        assert agg.confidence_weighted_score == pytest.approx(1.0)
        assert agg.unknown == 99

    def test_dropped_low_confidence_excluded_from_score(self) -> None:
        # 1 success at 0.9 + 1 super-low-confidence failure (dropped)
        agg = aggregate(
            [
                _obs(confidence=0.9, outcome=OutcomeKind.SUCCESS),
                _obs(confidence=0.1, outcome=OutcomeKind.FAILURE),
            ],
            category="strategy",
        )
        assert agg.confidence_weighted_score == pytest.approx(1.0)


# ── cold-start prior fallback ─────────────────────────────────────────────


class TestColdStart:
    def test_no_observations_uses_llm_prior_remapped_to_score_range(self) -> None:
        agg = aggregate([], category="strategy", source="llm_extracted")
        # prior 0.4 → score 2*0.4 - 1 = -0.2
        assert agg.confidence_weighted_score == pytest.approx(2.0 * COLD_START_PRIOR_LLM_EXTRACTED - 1.0)

    def test_no_observations_human_authored_uses_category_prior(self) -> None:
        agg = aggregate([], category="strategy", source="human_authored")
        expected = 2.0 * COLD_START_PRIOR_BY_CATEGORY["strategy"] - 1.0
        assert agg.confidence_weighted_score == pytest.approx(expected)

    def test_only_unknown_observations_falls_back_to_prior(self) -> None:
        # 5 unknowns count toward the unknown counter but do NOT replace the cold-start prior.
        agg = aggregate(
            [_obs(outcome=OutcomeKind.UNKNOWN) for _ in range(5)],
            category="recovery",
            source="human_authored",
        )
        assert agg.unknown == 5
        expected = 2.0 * COLD_START_PRIOR_BY_CATEGORY["recovery"] - 1.0
        assert agg.confidence_weighted_score == pytest.approx(expected)

    def test_one_measured_outcome_overrides_prior(self) -> None:
        # Single measured success should dominate the prior.
        agg = aggregate(
            [_obs(confidence=0.9, outcome=OutcomeKind.SUCCESS)],
            category="strategy",
            source="human_authored",
        )
        assert agg.confidence_weighted_score == pytest.approx(1.0)


# ── last_observed_at tracking ─────────────────────────────────────────────


class TestLastObservedAt:
    def test_none_for_empty_input(self) -> None:
        agg = aggregate([], category="strategy")
        assert agg.last_observed_at is None

    def test_picks_max_timestamp(self) -> None:
        observations = [
            _obs(when=0),
            _obs(when=300),  # 5 min later
            _obs(when=120),  # 2 min later
        ]
        agg = aggregate(observations, category="strategy")
        assert agg.last_observed_at == _ts(300)

    def test_unknown_observations_still_count_for_last_at(self) -> None:
        observations = [
            _obs(when=0, outcome=OutcomeKind.SUCCESS),
            _obs(when=600, outcome=OutcomeKind.UNKNOWN),
        ]
        agg = aggregate(observations, category="strategy")
        assert agg.last_observed_at == _ts(600)


# ── update_evidence: append + recompute ───────────────────────────────────


class TestUpdateEvidence:
    def test_appends_new_observations(self) -> None:
        existing = OutcomeEvidence(observations=[_obs(traj="old", confidence=0.9)])
        updated = update_evidence(
            existing,
            [_obs(traj="new", confidence=0.9, outcome=OutcomeKind.FAILURE)],
            category="strategy",
        )
        assert len(updated.observations) == 2
        assert updated.aggregated.confirmed_successes == 1
        assert updated.aggregated.confirmed_failures == 1

    def test_does_not_mutate_input(self) -> None:
        existing = OutcomeEvidence(observations=[_obs(traj="old")])
        before_len = len(existing.observations)
        _ = update_evidence(existing, [_obs(traj="new")], category="strategy")
        assert len(existing.observations) == before_len

    def test_idempotent_aggregate_when_no_new_observations(self) -> None:
        existing = OutcomeEvidence(
            observations=[_obs(confidence=0.9, outcome=OutcomeKind.SUCCESS)],
        )
        # Re-aggregate with no additions; should produce same score.
        updated = update_evidence(existing, [], category="strategy")
        assert updated.aggregated.confidence_weighted_score == pytest.approx(1.0)
        assert len(updated.observations) == 1
