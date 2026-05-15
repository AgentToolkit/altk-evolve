"""Outcome-extraction pipeline (Phase 2).

Mines outcome signals from agent trajectories and folds them into the
`OutcomeEvidence` ledger on each guideline. Six signal sources are supported
(see `altk_evolve.schema.outcome_evidence.SignalSource`); this package
hosts the extractors that produce `OutcomeObservation`s plus the
aggregator that rolls them into `AggregatedOutcome`.

Submodules:
- `aggregator` — pure functions; observations + category prior → AggregatedOutcome.
- `tool_signals` — Phoenix span / message inspection for tool errors, retries.
- `trajectory_shape` — pattern detection (retry→success, max-iter, terminate).
"""
