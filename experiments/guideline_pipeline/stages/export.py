"""Export a namespace's guidelines as the static files benchmark harnesses read.

playbook         {"entries": [{"r": rule, "n": support, "e": "s" | "f" | "b"}, ...]}
retrieval index  {"core": [rule, ...],
                  "singletons": [{"rule", "source_task", "source_instruction"}, ...]}

Support and the core use the library's definitions (``support`` in metadata, 1
when absent; core is support >= core_support; min_support is a floor applied
before the split), so an exported index splits a namespace exactly as
``EvolveClient.select_guidelines`` does. The index carries no embeddings: the
retrieval harness embeds each ``source_instruction`` itself at load time.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from experiments.guideline_pipeline.guidelines import GuidelineRow
from experiments.guideline_pipeline.stages.lineage import resolve_source_tasks

# Evolve evidence -> playbook "e". None (unknown: the trajectory had no outcome, or
# the guideline predates evidence) is exported as "b", the code the earlier export
# used for merged guidelines with no recorded outcome. Pending confirmation that no
# harness reads "b" as "observed in both successes and failures".
EVIDENCE_CODES: Mapping[str | None, str] = {"success": "s", "failure": "f", "both": "b", None: "b"}


def evidence_code(row: GuidelineRow) -> str:
    """The playbook code for a guideline's evidence; an unrecognized value is an error, not a guess."""
    evidence = row.metadata.get("evidence")
    if evidence not in EVIDENCE_CODES:
        raise ValueError(f"guideline {row.entity.id} has unrecognized evidence {evidence!r} (expected one of success, failure, both)")
    return EVIDENCE_CODES[evidence]


def _floor(rows: list[GuidelineRow], min_support: int) -> tuple[list[GuidelineRow], int]:
    kept = [row for row in rows if row.support >= min_support]
    return kept, len(rows) - len(kept)


@dataclass
class PlaybookReport:
    entries: int = 0
    below_min_support: int = 0
    empty: int = 0
    supports: list[int] = field(default_factory=list)

    def summary(self) -> str:
        skipped = f"{self.below_min_support} below min-support" + (f", {self.empty} empty" if self.empty else "")
        return (
            f"playbook: {self.entries} entries ({skipped} skipped); support sum={sum(self.supports)} "
            f"max={max(self.supports, default=0)} | n>=2: {sum(n >= 2 for n in self.supports)} | "
            f"n>=3: {sum(n >= 3 for n in self.supports)}"
        )


def build_playbook(rows: list[GuidelineRow], *, min_support: int = 1, empty: int = 0) -> tuple[dict[str, Any], PlaybookReport]:
    """The playbook for rows (already ordered) at or above min_support."""
    kept, below = _floor(rows, min_support)
    entries = [{"r": row.rule, "n": row.support, "e": evidence_code(row)} for row in kept]
    report = PlaybookReport(entries=len(entries), below_min_support=below, empty=empty, supports=[row.support for row in kept])
    return {"entries": entries}, report


@dataclass
class RetrievalIndexReport:
    core: int = 0
    singletons: int = 0
    below_min_support: int = 0
    empty: int = 0
    partial: int = 0
    unresolved: Counter[str] = field(default_factory=Counter)
    unresolved_ids: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        text = (
            f"retrieval index: {self.core} core, {self.singletons} singletons; "
            f"{self.below_min_support} below min-support, {sum(self.unresolved.values())} singletons skipped unresolved"
        )
        if self.unresolved:
            text += " (" + ", ".join(f"{reason}: {count}" for reason, count in sorted(self.unresolved.items())) + ")"
        if self.partial:
            text += f"; {self.partial} singletons missing some source tasks"
        return text + (f"; {self.empty} empty skipped" if self.empty else "")


def build_retrieval_index(
    rows: list[GuidelineRow],
    *,
    core_support: int,
    min_support: int = 1,
    instructions: Mapping[str, str] | None = None,
    empty: int = 0,
) -> tuple[dict[str, Any], RetrievalIndexReport]:
    """Split rows (already ordered) into the always-on core and retrievable singletons.

    A singleton needs a non-empty source instruction (the harness embeds it), so
    one whose sources don't resolve is skipped and counted by reason, never emitted
    with an empty field. A singleton with several resolved source tasks also gets
    ``source_tasks``, which the harness matches against individually.
    """
    if min_support > core_support:
        raise ValueError(f"min-support ({min_support}) must be <= core-support ({core_support}), as Evolve's config requires")
    pool, below = _floor(rows, min_support)
    report = RetrievalIndexReport(below_min_support=below, empty=empty)
    core: list[str] = []
    singletons: list[dict[str, Any]] = []
    for row in pool:
        if row.support >= core_support:
            core.append(row.rule)
            continue
        resolution = resolve_source_tasks(row, instructions)
        if resolution.problem is not None:
            report.unresolved[resolution.problem] += 1
            report.unresolved_ids.append((row.entity.id, resolution.problem))
            continue
        report.partial += resolution.partial
        first = resolution.tasks[0]
        singleton: dict[str, Any] = {"rule": row.rule, **first}
        if len(resolution.tasks) > 1:
            singleton["source_tasks"] = resolution.tasks
        singletons.append(singleton)
    report.core, report.singletons = len(core), len(singletons)
    return {"core": core, "singletons": singletons}, report
