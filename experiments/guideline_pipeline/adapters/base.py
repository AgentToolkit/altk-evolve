"""What a dataset adapter produces, and the interface it implements."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class ConfigurableAdapter(Adapter, Protocol):
    """An adapter that accepts ``--adapter-option KEY=VALUE`` settings.

    configure validates the options (raising ValueError on unknown keys or bad
    values) and returns a configured copy; the registered instance is unchanged.
    """

    def configure(self, options: Mapping[str, str]) -> Adapter: ...


def option_names(adapter: str, key: str, value: str) -> set[str]:
    """A comma-separated filter option. One that names nothing would mean "no filter", so it is an error."""
    names = {item.strip() for item in value.split(",") if item.strip()}
    if not names:
        raise ValueError(f"{adapter} option {key} names nothing: {value!r}")
    return names


def option_boolean(adapter: str, key: str, value: str) -> bool:
    """A boolean option: 1/true/yes/on or 0/false/no/off (any case; empty is false). Anything else is an error."""
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"{adapter} option {key} must be true or false, not {value!r}")
