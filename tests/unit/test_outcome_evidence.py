"""Unit tests for outcome_evidence Pydantic schema (Phase 0 deliverable)."""

from datetime import datetime, timezone

import pytest

from altk_evolve.schema.outcome_evidence import (
    COLD_START_PRIOR_BY_CATEGORY,
    COLD_START_PRIOR_LLM_EXTRACTED,
    AggregatedOutcome,
    OutcomeEvidence,
    OutcomeKind,
    OutcomeObservation,
    SignalSource,
    compute_cold_start_prior,
)


pytestmark = pytest.mark.unit


def _ts() -> datetime:
    return datetime(2026, 5, 15, 14, 22, tzinfo=timezone.utc)


class TestOutcomeObservation:
    def test_minimum_fields(self) -> None:
        obs = OutcomeObservation(
            trajectory_id="traj-1",
            signal_source=SignalSource.TOOL_ERROR,
            observed_outcome=OutcomeKind.FAILURE,
            confidence=0.95,
            observed_at=_ts(),
        )
        assert obs.trajectory_id == "traj-1"
        assert obs.detail is None

    def test_with_detail(self) -> None:
        obs = OutcomeObservation(
            trajectory_id="traj-1",
            signal_source=SignalSource.TOOL_ERROR,
            observed_outcome=OutcomeKind.FAILURE,
            confidence=0.9,
            observed_at=_ts(),
            detail="auth_handler 401",
        )
        assert obs.detail == "auth_handler 401"

    def test_confidence_must_be_in_range(self) -> None:
        for bad in (-0.1, 1.1, 2.0):
            with pytest.raises(Exception):  # noqa: B017 — pydantic validation error type varies
                OutcomeObservation(
                    trajectory_id="t",
                    signal_source=SignalSource.LLM_JUDGE,
                    observed_outcome=OutcomeKind.SUCCESS,
                    confidence=bad,
                    observed_at=_ts(),
                )

    def test_all_signal_sources_are_accepted(self) -> None:
        for src in SignalSource:
            obs = OutcomeObservation(
                trajectory_id="t",
                signal_source=src,
                observed_outcome=OutcomeKind.SUCCESS,
                confidence=0.5,
                observed_at=_ts(),
            )
            assert obs.signal_source == src


class TestAggregatedOutcome:
    def test_default_neutral(self) -> None:
        agg = AggregatedOutcome()
        assert agg.confirmed_successes == 0
        assert agg.confirmed_failures == 0
        assert agg.unknown == 0
        # 0.5 = neutral / no data on the [0, 1] probability-of-success scale.
        assert agg.confidence_weighted_score == 0.5
        assert agg.last_observed_at is None

    def test_score_bounds(self) -> None:
        for bad in (-0.1, 1.1):
            with pytest.raises(Exception):  # noqa: B017
                AggregatedOutcome(confidence_weighted_score=bad)

    def test_negative_counters_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            AggregatedOutcome(confirmed_successes=-1)


class TestOutcomeEvidence:
    def test_default_empty(self) -> None:
        ev = OutcomeEvidence()
        assert ev.observations == []
        assert isinstance(ev.aggregated, AggregatedOutcome)
        assert ev.aggregated.unknown == 0

    def test_serialization_round_trip(self) -> None:
        ev = OutcomeEvidence(
            observations=[
                OutcomeObservation(
                    trajectory_id="traj-1",
                    signal_source=SignalSource.IMPLICIT_USAGE,
                    observed_outcome=OutcomeKind.SUCCESS,
                    confidence=0.6,
                    observed_at=_ts(),
                )
            ],
            aggregated=AggregatedOutcome(
                inferred_successes=1,
                confidence_weighted_score=0.6,
                last_observed_at=_ts(),
            ),
        )
        as_json = ev.model_dump_json()
        round_trip = OutcomeEvidence.model_validate_json(as_json)
        assert round_trip.observations[0].trajectory_id == "traj-1"
        assert round_trip.aggregated.inferred_successes == 1

    def test_unbounded_growth_caught(self) -> None:
        many = [
            OutcomeObservation(
                trajectory_id=f"t-{i}",
                signal_source=SignalSource.IMPLICIT_USAGE,
                observed_outcome=OutcomeKind.UNKNOWN,
                confidence=0.0,
                observed_at=_ts(),
            )
            for i in range(10_001)
        ]
        with pytest.raises(Exception):  # noqa: B017
            OutcomeEvidence(observations=many)


class TestColdStartPriors:
    def test_llm_extracted_default(self) -> None:
        assert compute_cold_start_prior(category="strategy") == COLD_START_PRIOR_LLM_EXTRACTED
        assert compute_cold_start_prior(category="anything", source="llm_extracted") == COLD_START_PRIOR_LLM_EXTRACTED

    def test_human_authored_per_category(self) -> None:
        assert compute_cold_start_prior(category="strategy", source="human_authored") == COLD_START_PRIOR_BY_CATEGORY["strategy"]
        assert compute_cold_start_prior(category="recovery", source="human_authored") == COLD_START_PRIOR_BY_CATEGORY["recovery"]
        assert compute_cold_start_prior(category="optimization", source="human_authored") == COLD_START_PRIOR_BY_CATEGORY["optimization"]

    def test_unknown_category_falls_back_to_llm_prior(self) -> None:
        assert compute_cold_start_prior(category="totally_new", source="human_authored") == COLD_START_PRIOR_LLM_EXTRACTED

    def test_priors_are_in_unit_interval(self) -> None:
        for v in COLD_START_PRIOR_BY_CATEGORY.values():
            assert 0.0 <= v <= 1.0
        assert 0.0 <= COLD_START_PRIOR_LLM_EXTRACTED <= 1.0
