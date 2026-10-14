"""Lineage: each guideline -> metadata.sources -> its source tasks (and, given the dataset, their instructions).

Evolve records provenance as ``metadata["sources"]``: one content-free entry per
contributing source (``conversation_id``, ``task_id``, ``user_id``, ``agent_id``,
``status``, ``associated_at``). After ``mine``, ``task_id`` is the adapter
record's ``trace_id``. The task instruction is not part of a source entry; the
only copy in the store is the guideline's ``task_description``, which describes
the guideline as a whole. So an instruction per source task comes from the
dataset itself: pass the adapter and ``--input`` the namespace was mined from.

Guidelines written by consolidation carry no sources on this version of Evolve,
so they are reported as ``missing`` rather than skipped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from altk_evolve.processing import Trajectory
from altk_evolve.schema.guidelines import DEFAULT_TASK_DESCRIPTION
from altk_evolve.schema.provenance import sources as library_sources

from experiments.guideline_pipeline.guidelines import GuidelineRow

SourcesOrigin = Literal["recorded", "derived", "missing"]


def sources_origin(row: GuidelineRow) -> SourcesOrigin:
    """recorded: metadata["sources"] is set; derived: rebuilt from legacy source_task_id fields; missing: neither."""
    recorded = row.metadata.get("sources")
    if isinstance(recorded, list) and recorded:
        return "recorded"
    return "derived" if library_sources(row.entity) else "missing"


def supporting_task_ids(row: GuidelineRow) -> list[str]:
    """Source task ids still backing the current content (superseded sources excluded), first-seen order."""
    task_ids = (source.get("task_id") for source in library_sources(row.entity) if source.get("status") != "superseded")
    return list(dict.fromkeys(str(task_id) for task_id in task_ids if task_id))


def task_instruction(trajectory: Trajectory) -> str | None:
    """The first user message, supporting context first, as the standard extractor reads the task.

    None when that message is not plain text: a later message is not the task.
    """
    for message in [*trajectory.context_messages, *trajectory.messages]:
        if message.get("role") == "user":
            content = message.get("content")
            return content.strip() or None if isinstance(content, str) else None
    return None


def load_instructions(records: Iterable[Trajectory]) -> dict[str, str]:
    """trace_id -> task instruction for every record that has both; the first record per trace_id wins."""
    instructions: dict[str, str] = {}
    for record in records:
        instruction = task_instruction(record)
        if record.trace_id and instruction and record.trace_id not in instructions:
            instructions[record.trace_id] = instruction
    return instructions


def _usable_description(row: GuidelineRow) -> str | None:
    description = row.metadata.get("task_description")
    if isinstance(description, str) and description.strip() and description != DEFAULT_TASK_DESCRIPTION:
        return description.strip()
    return None


@dataclass(frozen=True)
class SourceResolution:
    """A guideline's source tasks with instructions; problem says why none resolved, partial if some did not."""

    tasks: list[dict[str, str]]
    problem: str | None = None
    partial: bool = False


def resolve_source_tasks(row: GuidelineRow, instructions: Mapping[str, str] | None) -> SourceResolution:
    """Pair each supporting source task with its instruction.

    With instructions (from the dataset), every source task is looked up by id.
    Without, the guideline's own task_description is used, and only when the
    guideline has exactly one source task, since only then is it known to describe
    that task. Neither path guesses.
    """
    task_ids = supporting_task_ids(row)
    if not task_ids:
        return SourceResolution([], "no source task")
    if instructions is not None:
        tasks = [{"source_task": task, "source_instruction": instructions[task]} for task in task_ids if task in instructions]
        problem = "source task not in --input"
    else:
        description = _usable_description(row) if len(task_ids) == 1 else None
        tasks = [] if description is None else [{"source_task": task_ids[0], "source_instruction": description}]
        problem = "no stored instruction (pass --adapter and --input)"
    if not tasks:
        return SourceResolution([], problem)
    return SourceResolution(tasks, partial=len(tasks) < len(task_ids))


@dataclass
class LineageReport:
    guidelines: int = 0
    origins: dict[str, int] = field(default_factory=lambda: {"recorded": 0, "derived": 0, "missing": 0})
    source_tasks: int = 0
    instructions_found: int | None = None
    empty: int = 0

    def summary(self) -> str:
        text = (
            f"lineage: {self.guidelines} guidelines ({self.origins['recorded']} with recorded sources, "
            f"{self.origins['derived']} derived from source_task_id, {self.origins['missing']} without sources); "
            f"{self.source_tasks} distinct source tasks"
        )
        if self.instructions_found is not None:
            text += f", {self.instructions_found} found in --input"
        return text + (f"; {self.empty} empty guidelines skipped" if self.empty else "")


def _processing(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    processing = metadata.get("processing")
    if not isinstance(processing, Mapping):
        return None
    return {key: processing.get(key) for key in ("profile_id", "revision", "processor_id", "source_batch")}


def build_lineage(
    rows: list[GuidelineRow], namespace_id: str, *, instructions: Mapping[str, str] | None = None, empty: int = 0
) -> tuple[dict[str, Any], LineageReport]:
    """The lineage document for rows (in their order) and its counts.

    Each entry repeats the guideline's stored sources verbatim; with instructions,
    each source whose task_id was found gains an ``instruction``.
    """
    report = LineageReport(guidelines=len(rows), empty=empty)
    all_tasks: set[str] = set()
    entries = []
    for row in rows:
        origin = sources_origin(row)
        report.origins[origin] += 1
        stored = library_sources(row.entity)
        if instructions is not None:
            for source in stored:
                task = source.get("task_id")
                if task is not None and str(task) in instructions:
                    source["instruction"] = instructions[str(task)]
        task_ids = list(dict.fromkeys(str(s["task_id"]) for s in stored if s.get("task_id")))
        all_tasks.update(task_ids)
        entries.append(
            {
                "id": row.entity.id,
                "guideline": row.rule,
                "support": row.support,
                "evidence": row.metadata.get("evidence"),
                "sources_origin": origin,
                "provenance_incomplete": bool(row.metadata.get("provenance_incomplete", origin == "missing")),
                "source_task_ids": task_ids,
                "sources": stored,
                "task_description": row.metadata.get("task_description"),
                "processing": _processing(row.metadata),
            }
        )
    report.source_tasks = len(all_tasks)
    if instructions is not None:
        report.instructions_found = len(all_tasks & set(instructions))
    document = {"namespace": namespace_id, "count": len(entries), **report.origins, "guidelines": entries}
    return document, report
