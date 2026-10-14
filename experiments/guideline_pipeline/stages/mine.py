"""Mine: adapter records -> Trajectory -> EvolveClient.process_trajectory.

Every record goes through a processing profile, so generated guidelines carry
profile provenance, memory hooks run, and each record's batch is checkpointed:
re-running the same input skips what already committed and retries the rest.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import islice

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.processing import ProcessingPlan

from experiments.guideline_pipeline.adapters.base import AdapterRecord


@dataclass
class MineReport:
    """Counts for one run. Skipped means every processor had already checkpointed the record's batch."""

    dry_run: bool
    profile_id: str | None = None
    profile_revision: int | None = None
    processed: int = 0
    skipped: int = 0
    entities: int = 0
    events: Counter[str] = field(default_factory=Counter)
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def updates(self) -> int:
        """Entries in ProcessingResult.updates across processed records; events breaks them down (ADD, UPDATE, DELETE, NONE)."""
        return sum(self.events.values())

    def summary(self) -> str:
        if self.dry_run:
            return f"dry run: {self.processed} valid, {len(self.failures)} failed"
        events = ", ".join(f"{name}={count}" for name, count in sorted(self.events.items()))
        return (
            f"profile {self.profile_id} r{self.profile_revision}: {self.processed} processed, "
            f"{self.skipped} skipped (checkpointed), {len(self.failures)} failed; "
            f"{self.entities} entities proposed, {self.updates} updates" + (f" ({events})" if events else "")
        )


def mine(
    records: Iterable[AdapterRecord],
    client: EvolveClient | None,
    *,
    namespace_id: str,
    processing_profile: str,
    revision: int | None = None,
    limit: int | None = None,
) -> MineReport:
    """Process up to limit records into namespace_id; client=None is a dry run.

    The profile is resolved once, so the whole run uses one revision. A failing
    record is reported and the run continues; an adapter that fails while reading
    ends the run, keeping what already committed.
    """
    report = MineReport(dry_run=client is None)
    plan: ProcessingPlan | None = None
    if client is not None:
        if not client.backend.supports_atomic_writes:
            # Batch checkpoints are committed with their outputs; Milvus rejects identified batches.
            raise ValueError(f"mine needs a backend with atomic writes (filesystem or postgres), not {client.config.backend}")
        plan = client.processing.resolve(processing_profile, revision=revision)
        report.profile_id, report.profile_revision = plan.profile_id, plan.revision
        client.ensure_namespace(namespace_id)
    try:
        for record in islice(records, limit):
            _mine_one(record, client, plan, namespace_id, report)
    except Exception as exc:
        report.failures.append(("<adapter>", f"{type(exc).__name__}: {exc}"))
    return report


def _mine_one(
    record: AdapterRecord, client: EvolveClient | None, plan: ProcessingPlan | None, namespace_id: str, report: MineReport
) -> None:
    try:
        trajectory = record.to_trajectory()
        trajectory.model_dump_json()  # fail here, not mid-run, on content an LLM request can't carry
        if client is None or plan is None:
            report.processed += 1
            return
        result = client.process_trajectory(trajectory, namespace_id=namespace_id, plan=plan)
    except Exception as exc:
        report.failures.append((record.trace_id, f"{type(exc).__name__}: {exc}"))
        return
    if result.skipped_processors and not result.completed_processors:
        report.skipped += 1
    else:
        report.processed += 1
    report.entities += len(result.entities)
    report.events.update(update["event"] for update in result.updates)
