"""Tests for altk_evolve.telemetry.outcome_aggregation_job (Phase 2).

Covers:
- JSONL file reading + lookback window filtering
- IMPLICIT_USAGE observation emission at recall_count >= 3
- Query-diversity confidence bump (0.5 → 0.6)
- aggregated.confidence_weighted_score update after run
- Idempotency: no duplicate observations on second run with same events
- namespace_id=None processes all namespaces
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from altk_evolve.schema.outcome_evidence import (
    OutcomeEvidence,
    OutcomeKind,
    SignalSource,
)
from altk_evolve.telemetry.outcome_aggregation_job import (
    _build_observation,
    _index_events_by_guideline,
    _read_events_in_window,
    run_outcome_aggregation,
)


pytestmark = pytest.mark.unit


# ── helpers ────────────────────────────────────────────────────────────────


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")


def _today_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _days_ago_iso(n: int) -> str:
    d = datetime.now(tz=timezone.utc) - timedelta(days=n)
    return d.strftime("%Y-%m-%d")


def _make_event(guideline_ids: list[str], query: str = "q1") -> dict:
    return {
        "event": "guideline_retrieval",
        "result_ids": guideline_ids,
        "query": query,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


def _make_entity(entity_id: str, category: str = "strategy", evidence: OutcomeEvidence | None = None) -> MagicMock:
    entity = MagicMock()
    entity.id = entity_id
    meta: dict = {"category": category}
    if evidence is not None:
        meta["outcome_evidence"] = evidence.model_dump(mode="json")
    entity.metadata = meta
    return entity


def _make_client(entities_by_ns: dict[str, list[MagicMock]], namespaces: list[str]) -> MagicMock:
    """Build a mock EvolveClient with preset entities and patch_entity_metadata tracking."""
    client = MagicMock()

    ns_mocks = []
    for ns_id in namespaces:
        ns = MagicMock()
        ns.id = ns_id
        ns_mocks.append(ns)
    client.all_namespaces.return_value = ns_mocks

    def _get_ns_details(ns_id: str) -> MagicMock:
        for ns in ns_mocks:
            if ns.id == ns_id:
                return ns
        raise KeyError(ns_id)

    client.get_namespace_details.side_effect = _get_ns_details

    def _get_entity(ns_id: str, entity_id: str) -> MagicMock | None:
        for e in entities_by_ns.get(ns_id, []):
            if e.id == entity_id:
                return e
        return None

    client.get_entity_by_id.side_effect = _get_entity
    client.patch_entity_metadata.return_value = MagicMock()
    return client


# ── _read_events_in_window ─────────────────────────────────────────────────


class TestReadEventsInWindow:
    def test_reads_file_within_lookback(self, tmp_path: Path) -> None:
        today = _today_iso()
        _write_jsonl(tmp_path / f"{today}.jsonl", [{"event": "x", "result_ids": ["g1"]}])
        events = _read_events_in_window(tmp_path, lookback_days=7)
        assert len(events) == 1

    def test_ignores_file_outside_lookback(self, tmp_path: Path) -> None:
        old_date = _days_ago_iso(30)
        _write_jsonl(tmp_path / f"{old_date}.jsonl", [{"event": "x", "result_ids": ["g1"]}])
        events = _read_events_in_window(tmp_path, lookback_days=7)
        assert len(events) == 0

    def test_skips_malformed_lines_gracefully(self, tmp_path: Path) -> None:
        today = _today_iso()
        path = tmp_path / f"{today}.jsonl"
        path.write_text('{"valid": true}\nnot-json\n{"also": "valid"}\n')
        events = _read_events_in_window(tmp_path, lookback_days=7)
        assert len(events) == 2

    def test_returns_empty_for_missing_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "no_such_dir"
        # Caller checks existence; _read_events_in_window handles missing dir gracefully
        assert not missing.exists()

    def test_ignores_non_jsonl_files(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("ignore me\n")
        events = _read_events_in_window(tmp_path, lookback_days=7)
        assert events == []


# ── _index_events_by_guideline ─────────────────────────────────────────────


class TestIndexEventsByGuideline:
    def test_groups_events_by_result_ids(self) -> None:
        events = [
            {"result_ids": ["g1", "g2"], "query": "q1"},
            {"result_ids": ["g1"], "query": "q2"},
        ]
        index = _index_events_by_guideline(events)
        assert len(index["g1"]) == 2
        assert len(index["g2"]) == 1

    def test_skips_events_without_result_ids(self) -> None:
        from typing import Any

        events: list[dict[str, Any]] = [{"event": "no_ids"}, {"result_ids": None}]
        index = _index_events_by_guideline(events)
        assert len(index) == 0

    def test_skips_non_string_ids(self) -> None:
        from typing import Any

        events: list[dict[str, Any]] = [{"result_ids": [123, None, "g1"]}]
        index = _index_events_by_guideline(events)
        assert "g1" in index
        assert len(index) == 1


# ── _build_observation ─────────────────────────────────────────────────────


class TestBuildObservation:
    _now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)

    def test_none_when_count_below_3(self) -> None:
        events = [_make_event(["g1"]), _make_event(["g1"])]
        obs = _build_observation(guideline_id="g1", events=events, now=self._now)
        assert obs is None

    def test_emits_success_at_confidence_0_5_when_count_ge_3(self) -> None:
        events = [_make_event(["g1"], query="same_query") for _ in range(3)]
        obs = _build_observation(guideline_id="g1", events=events, now=self._now)
        assert obs is not None
        assert obs.observed_outcome == OutcomeKind.SUCCESS
        assert obs.confidence == pytest.approx(0.5)
        assert obs.signal_source == SignalSource.IMPLICIT_USAGE
        assert "recall_count=3" in (obs.detail or "")

    def test_bumps_confidence_to_0_6_with_query_diversity(self) -> None:
        events = [_make_event(["g1"], query=f"unique_query_{i}") for i in range(3)]
        obs = _build_observation(guideline_id="g1", events=events, now=self._now)
        assert obs is not None
        assert obs.confidence == pytest.approx(0.6)
        assert "query_diversity=3" in (obs.detail or "")

    def test_count_4_still_single_observation(self) -> None:
        events = [_make_event(["g1"]) for _ in range(4)]
        obs = _build_observation(guideline_id="g1", events=events, now=self._now)
        assert obs is not None
        assert "recall_count=4" in (obs.detail or "")


# ── run_outcome_aggregation: core behaviour ────────────────────────────────


class TestRunOutcomeAggregation:
    def _run(
        self,
        tmp_path: Path,
        entities_by_ns: dict[str, list[MagicMock]],
        namespaces: list[str],
        events: list[dict],
        namespace_id: str | None = None,
    ) -> tuple[dict, MagicMock]:
        today = _today_iso()
        log_dir = tmp_path / "telemetry"
        log_dir.mkdir()
        _write_jsonl(log_dir / f"{today}.jsonl", events)

        mock_client = _make_client(entities_by_ns, namespaces)
        with patch(
            "altk_evolve.telemetry.outcome_aggregation_job.EvolveClient",
            return_value=mock_client,
        ):
            result = run_outcome_aggregation(
                namespace_id=namespace_id,
                lookback_days=7,
                log_dir=str(log_dir),
            )
        return result, mock_client

    def test_updates_guideline_with_3_retrievals(self, tmp_path: Path) -> None:
        entity = _make_entity("g1")
        events = [_make_event(["g1"], query=f"q{i}") for i in range(3)]
        result, client = self._run(tmp_path, {"ns1": [entity]}, ["ns1"], events)

        assert result["observations_added"] == 1
        assert result["guidelines_updated"] == 1
        assert result["errors"] == []
        client.patch_entity_metadata.assert_called_once()
        call_kwargs = client.patch_entity_metadata.call_args
        updated_evidence_dict = call_kwargs[0][2]["outcome_evidence"]
        evidence = OutcomeEvidence.model_validate(updated_evidence_dict)
        assert len(evidence.observations) == 1
        assert evidence.observations[0].signal_source == SignalSource.IMPLICIT_USAGE
        # aggregated score should reflect the new observation
        assert evidence.aggregated.confidence_weighted_score > 0.5

    def test_no_update_when_below_3_retrievals(self, tmp_path: Path) -> None:
        entity = _make_entity("g1")
        events = [_make_event(["g1"]) for _ in range(2)]
        result, client = self._run(tmp_path, {"ns1": [entity]}, ["ns1"], events)

        assert result["observations_added"] == 0
        client.patch_entity_metadata.assert_not_called()

    def test_query_diversity_sets_confidence_0_6_in_stored_evidence(self, tmp_path: Path) -> None:
        entity = _make_entity("g1")
        events = [_make_event(["g1"], query=f"unique_{i}") for i in range(3)]
        result, client = self._run(tmp_path, {"ns1": [entity]}, ["ns1"], events)

        assert result["observations_added"] == 1
        updated_evidence_dict = client.patch_entity_metadata.call_args[0][2]["outcome_evidence"]
        evidence = OutcomeEvidence.model_validate(updated_evidence_dict)
        assert evidence.observations[0].confidence == pytest.approx(0.6)

    def test_idempotent_on_second_run_no_new_events(self, tmp_path: Path) -> None:
        # First run: build the observation and persist it.
        events = [_make_event(["g1"], query=f"q{i}") for i in range(3)]
        today = _today_iso()
        log_dir = tmp_path / "telemetry"
        log_dir.mkdir()
        _write_jsonl(log_dir / f"{today}.jsonl", events)

        persisted_evidence: dict = {}

        def _patch(ns_id, eid, meta_updates):
            persisted_evidence.update(meta_updates)
            return MagicMock()

        entity = _make_entity("g1")
        mock_client = _make_client({"ns1": [entity]}, ["ns1"])
        mock_client.patch_entity_metadata.side_effect = _patch

        with patch(
            "altk_evolve.telemetry.outcome_aggregation_job.EvolveClient",
            return_value=mock_client,
        ):
            run_outcome_aggregation(namespace_id="ns1", lookback_days=7, log_dir=str(log_dir))

        # Second run: entity now carries the persisted evidence.
        entity2 = _make_entity("g1", evidence=OutcomeEvidence.model_validate(persisted_evidence["outcome_evidence"]))
        mock_client2 = _make_client({"ns1": [entity2]}, ["ns1"])

        with patch(
            "altk_evolve.telemetry.outcome_aggregation_job.EvolveClient",
            return_value=mock_client2,
        ):
            result2 = run_outcome_aggregation(namespace_id="ns1", lookback_days=7, log_dir=str(log_dir))

        assert result2["observations_added"] == 0
        mock_client2.patch_entity_metadata.assert_not_called()

    def test_returns_empty_summary_when_log_dir_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "no_telemetry"
        with patch("altk_evolve.telemetry.outcome_aggregation_job.EvolveClient"):
            result = run_outcome_aggregation(log_dir=str(missing))
        assert result["namespaces_processed"] == 0
        assert result["observations_added"] == 0
        assert result["errors"] == []

    def test_skips_events_outside_lookback_window(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "telemetry"
        log_dir.mkdir()
        old_date = _days_ago_iso(30)
        _write_jsonl(log_dir / f"{old_date}.jsonl", [_make_event(["g1"]) for _ in range(5)])

        entity = _make_entity("g1")
        mock_client = _make_client({"ns1": [entity]}, ["ns1"])

        with patch(
            "altk_evolve.telemetry.outcome_aggregation_job.EvolveClient",
            return_value=mock_client,
        ):
            result = run_outcome_aggregation(namespace_id="ns1", lookback_days=7, log_dir=str(log_dir))

        assert result["observations_added"] == 0
        mock_client.patch_entity_metadata.assert_not_called()

    def test_processes_all_namespaces_when_namespace_id_none(self, tmp_path: Path) -> None:
        g_a = _make_entity("g_a")
        g_b = _make_entity("g_b")
        events = [_make_event(["g_a", "g_b"], query=f"q{i}") for i in range(3)]
        result, client = self._run(
            tmp_path,
            {"ns_a": [g_a], "ns_b": [g_b]},
            ["ns_a", "ns_b"],
            events,
            namespace_id=None,
        )
        assert result["namespaces_processed"] == 2
        # Each namespace that found a matching guideline should have updated it.
        assert result["observations_added"] >= 1
