"""Daily outcome aggregation job: telemetry → implicit-usage signals (Phase 2).

Reads the date-partitioned JSONL retrieval log produced by RetrievalLog,
derives per-guideline IMPLICIT_USAGE observations, and persists them back
to the guideline's `metadata["outcome_evidence"]` via EvolveClient.

Heuristics (Phase 2 v1):
  - Count distinct retrieval events per guideline_id over the lookback window.
  - If count >= 3 → emit SUCCESS observation at confidence=0.5
    (detail: "recall_count={N}").
  - If distinct query count >= 3 → bump confidence to 0.6
    (detail: "recall_count={N} query_diversity={M}").
  - No negative observation when count==0 (Phase 2 v1 skip).

Idempotency: each observation is hashed from (guideline_id, trajectory_id,
signal_source, observed_at ISO, detail). The job skips any observation whose
hash is already present in the persisted ledger, so re-runs with the same
input are no-ops.

Scheduling: the caller is responsible for scheduling (cron, systemd, etc.).
This function is a pure synchronous job — it does not spawn threads or daemons.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from altk_evolve.config.markdown import MarkdownSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.llm.outcome_extraction.aggregator import update_evidence
from altk_evolve.schema.outcome_evidence import (
    OutcomeEvidence,
    OutcomeKind,
    OutcomeObservation,
    SignalSource,
)

logger = logging.getLogger(__name__)


def _observation_hash(
    *,
    trajectory_id: str,
    signal_source: str,
    detail: str | None,
) -> str:
    """Stable dedup key for IMPLICIT_USAGE observations.

    Intentionally excludes observed_at so that re-running the job with the
    same telemetry input produces the same hash and is correctly identified
    as a duplicate, regardless of when the job runs.
    """
    key = f"{trajectory_id}|{signal_source}|{detail or ''}"
    return hashlib.sha256(key.encode()).hexdigest()


def _existing_hashes(evidence: OutcomeEvidence) -> set[str]:
    hashes: set[str] = set()
    for obs in evidence.observations:
        h = _observation_hash(
            trajectory_id=obs.trajectory_id,
            signal_source=obs.signal_source.value,
            detail=obs.detail,
        )
        hashes.add(h)
    return hashes


def _read_events_in_window(log_dir: Path, lookback_days: int) -> list[dict[str, Any]]:
    """Return all parsed JSONL events from files within the lookback window."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    events: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("*.jsonl")):
        # filename is YYYY-MM-DD.jsonl
        stem = path.stem
        try:
            file_date = datetime.strptime(stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("skipping malformed JSONL line in %s: %s", path, exc)
        except OSError as exc:
            logger.warning("could not read telemetry file %s: %s", path, exc)
    return events


def _index_events_by_guideline(
    events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Map guideline_id → list of retrieval events that returned it."""
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        result_ids = event.get("result_ids")
        if not isinstance(result_ids, list):
            continue
        for gid in result_ids:
            if isinstance(gid, str) and gid:
                index[gid].append(event)
    return index


def _build_observation(
    *,
    guideline_id: str,
    events: list[dict[str, Any]],
    now: datetime,
) -> OutcomeObservation | None:
    """Build a single IMPLICIT_USAGE observation for a guideline, or None if below threshold."""
    count = len(events)
    if count < 3:
        return None

    distinct_queries = {e.get("query", "") for e in events if e.get("query")}
    confidence = 0.6 if len(distinct_queries) >= 3 else 0.5

    # trajectory_id: use the guideline_id as a stable pseudo-trajectory for
    # implicit-usage observations, since there is no single source trajectory.
    trajectory_id = f"implicit:{guideline_id}"
    if len(distinct_queries) >= 3:
        detail = f"recall_count={count} query_diversity={len(distinct_queries)}"
    else:
        detail = f"recall_count={count}"

    return OutcomeObservation(
        trajectory_id=trajectory_id,
        signal_source=SignalSource.IMPLICIT_USAGE,
        observed_outcome=OutcomeKind.SUCCESS,
        confidence=confidence,
        observed_at=now,
        detail=detail,
    )


def _get_or_init_evidence(entity_metadata: dict[str, Any]) -> OutcomeEvidence:
    raw = entity_metadata.get("outcome_evidence")
    if isinstance(raw, dict):
        try:
            return OutcomeEvidence.model_validate(raw)
        except Exception as exc:
            logger.warning("could not parse existing outcome_evidence: %s; starting fresh", exc)
    return OutcomeEvidence()


def run_outcome_aggregation(
    *,
    namespace_id: str | None = None,
    lookback_days: int = 7,
    log_dir: str | None = None,
) -> dict[str, Any]:
    """Read retrieval_log JSONL, derive IMPLICIT_USAGE observations per guideline,
    append to each guideline's outcome_evidence.observations, recompute aggregated.

    Args:
        namespace_id: Restrict to a single namespace. None processes all namespaces.
        lookback_days: How many calendar days of telemetry to read (default 7).
        log_dir: Override the telemetry directory. Defaults to
            `{MarkdownSettings().data_dir}/telemetry`.

    Returns:
        {
            "namespaces_processed": int,
            "guidelines_updated": int,
            "observations_added": int,
            "errors": list[str],
        }

    The caller is responsible for scheduling this function (cron, systemd, etc.).
    """
    summary: dict[str, Any] = {
        "namespaces_processed": 0,
        "guidelines_updated": 0,
        "observations_added": 0,
        "errors": [],
    }

    # Resolve log directory.
    if log_dir is None:
        settings = MarkdownSettings()
        resolved_log_dir = Path(settings.data_dir) / "telemetry"
    else:
        resolved_log_dir = Path(log_dir)

    if not resolved_log_dir.exists():
        logger.info("telemetry log_dir %s does not exist; nothing to aggregate", resolved_log_dir)
        return summary

    events = _read_events_in_window(resolved_log_dir, lookback_days)
    if not events:
        logger.info("no telemetry events in lookback window (%d days)", lookback_days)
        return summary

    events_by_guideline = _index_events_by_guideline(events)
    if not events_by_guideline:
        logger.info("no guideline result_ids found in telemetry events")
        return summary

    client = EvolveClient()
    now = datetime.now(tz=timezone.utc)

    namespaces = client.all_namespaces(limit=1000) if namespace_id is None else [client.get_namespace_details(namespace_id)]

    for ns in namespaces:
        try:
            _process_namespace(
                client=client,
                namespace_id=ns.id,
                events_by_guideline=events_by_guideline,
                now=now,
                summary=summary,
            )
            summary["namespaces_processed"] += 1
        except Exception as exc:
            msg = f"namespace {ns.id}: {exc}"
            logger.warning("outcome aggregation failed for %s", msg, exc_info=True)
            summary["errors"].append(msg)

    return summary


def _process_namespace(
    *,
    client: Any,
    namespace_id: str,
    events_by_guideline: dict[str, list[dict[str, Any]]],
    now: datetime,
    summary: dict[str, Any],
) -> None:
    # Only fetch guidelines that have retrieval events; avoids full table scans.
    for guideline_id, events in events_by_guideline.items():
        try:
            entity = client.get_entity_by_id(namespace_id, guideline_id)
        except Exception as exc:
            logger.debug("get_entity_by_id(%s, %s) failed: %s", namespace_id, guideline_id, exc)
            entity = None

        if entity is None:
            continue

        observation = _build_observation(guideline_id=guideline_id, events=events, now=now)
        if observation is None:
            continue

        metadata = entity.metadata or {}
        evidence = _get_or_init_evidence(metadata)

        # Dedup: skip if an observation with the same stable key already exists.
        obs_hash = _observation_hash(
            trajectory_id=observation.trajectory_id,
            signal_source=observation.signal_source.value,
            detail=observation.detail,
        )
        if obs_hash in _existing_hashes(evidence):
            logger.debug("skipping duplicate observation for guideline %s", guideline_id)
            continue

        category = metadata.get("category", "strategy")
        updated_evidence = update_evidence(evidence, [observation], category=category)

        try:
            client.patch_entity_metadata(
                namespace_id,
                guideline_id,
                {"outcome_evidence": updated_evidence.model_dump(mode="json")},
            )
            summary["guidelines_updated"] += 1
            summary["observations_added"] += 1
        except Exception as exc:
            msg = f"patch_entity_metadata({namespace_id}, {guideline_id}): {exc}"
            logger.warning("failed to persist outcome_evidence: %s", msg, exc_info=True)
            summary["errors"].append(msg)
