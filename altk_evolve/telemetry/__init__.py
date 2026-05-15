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
