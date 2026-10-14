"""What a dataset adapter produces, and the interface it implements."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from pydantic import Field

from altk_evolve.processing import Trajectory, TrajectoryBatch


class AdapterRecord(Trajectory):
    """One source task, ready to process: a Trajectory whose identity is required.

    trace_id is the source task id. batch makes re-running ``mine`` idempotent:
    use a fixed source (the dataset), conversation_id (the task), batch_id (the
    attempt or run within the task) and a revision that changes only when this
    record's content or outcome changes. Set outcome when the dataset has an
    evaluator verdict; it becomes each generated guideline's evidence.
    """

    batch: TrajectoryBatch
    trace_id: str = Field(min_length=1)

    def to_trajectory(self) -> Trajectory:
        return Trajectory.model_validate(self.model_dump())


class Adapter(Protocol):
    """Reads one dataset format. Register instances in ``adapters.ADAPTERS``."""

    name: str

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        """Yield records from path lazily, so large datasets stream."""
        ...
