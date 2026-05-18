"""Telemetry pipeline for the Phase 2+ memory system.

Two distinct pipelines live here, separated deliberately:

- `retrieval_log` — best-effort, async, bounded, lossy JSONL writer. Drops
  oldest records on overflow. NEVER blocks the retrieval hot path. Used
  for qualitative review and as raw input for the daily aggregator.

- `durable_metrics` — gate-grade counters and histograms. Direct writes,
  not lossy. Used for the §11 graduation/rollback gates where biased
  data would mislead decisions. Drops are themselves a counter so we
  know when to fail open. (Phase 2.5 — coming next.)

The aggregator job folds JSONL events into `outcome_evidence.aggregated.*`
on each guideline once a day; gates evaluate on durable_metrics, never
on the lossy log directly. Codex review round-2 §4 finding.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from altk_evolve.telemetry.durable_metrics import DurableMetrics
    from altk_evolve.telemetry.retrieval_log import RetrievalLog

_lock = threading.Lock()
_retrieval_log: RetrievalLog | None = None
_durable_metrics: DurableMetrics | None = None


def get_retrieval_log() -> "RetrievalLog":
    """Return the process-wide RetrievalLog singleton (lazily initialized)."""
    global _retrieval_log
    if _retrieval_log is None:
        with _lock:
            if _retrieval_log is None:
                import os
                from altk_evolve.config.markdown import markdown_settings
                from altk_evolve.telemetry.retrieval_log import RetrievalLog

                log_dir = os.path.join(markdown_settings.data_dir, "telemetry")
                _retrieval_log = RetrievalLog(log_dir=log_dir)
    return _retrieval_log


def get_durable_metrics() -> "DurableMetrics":
    """Return the process-wide DurableMetrics singleton (lazily initialized)."""
    global _durable_metrics
    if _durable_metrics is None:
        with _lock:
            if _durable_metrics is None:
                from altk_evolve.telemetry.durable_metrics import DurableMetrics

                _durable_metrics = DurableMetrics()
    return _durable_metrics


def reset_telemetry_for_tests() -> None:
    """Reset process-level singletons. Call only from test teardown."""
    global _retrieval_log, _durable_metrics
    with _lock:
        if _retrieval_log is not None:
            try:
                _retrieval_log.close(timeout=0.5)
            except Exception:  # noqa: BLE001
                pass
            _retrieval_log = None
        _durable_metrics = None
