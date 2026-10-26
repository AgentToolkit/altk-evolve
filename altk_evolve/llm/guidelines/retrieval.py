"""Dosage-aware guideline selection: always-on core + per-task retrieved guidelines.

Instead of injecting the whole playbook every task (best only for strong models), this
selects a small, task-relevant dose (best for weaker models — the capability-dependent
dosage finding):

    core      = guidelines backed by >= ``core_support`` distinct source tasks (they recurred
                across many tasks, so they generalise) — always included.
    retrieved = up to ``top_k`` further guidelines whose SOURCE task (``task_description``)
                is most similar to the current task ("a lesson from a task like this one"),
                after dropping ones already covered by the core or duplicating each other.

``min_support`` applies a non-destructive support threshold (the sup2/sup3 filter) to the
candidate pool without deleting anything from the store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from altk_evolve.llm.guidelines.clustering import _get_sentence_transformer, stored_support, stored_tasks
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.schema.provenance import sources

logger = logging.getLogger(__name__)

SimilarityKey = Literal["source_task", "guideline_text"]


@dataclass(frozen=True)
class GuidelineSelection:
    """Result of :func:`select_guidelines` — core (always-on) plus retrieved guidelines."""

    core: list[RecordedEntity] = field(default_factory=list)
    retrieved: list[RecordedEntity] = field(default_factory=list)

    @property
    def all(self) -> list[RecordedEntity]:
        return [*self.core, *self.retrieved]


def _support(entity: RecordedEntity) -> int:
    """How many distinct source tasks back a guideline.

    Counted from its supporting ``sources`` associations, so rewordings merged from one
    task count once. That count is used only when every supporting source names a task and
    provenance is complete. Otherwise (provenance predates it, a merge took in a guideline
    with no known source, or a source records only a conversation) some support is not in
    the task count, so the stored ``support`` applies, but never less than the known tasks.
    A ``task_id`` that is not a string or an integer is ignored.
    """
    task_ids = [source.get("task_id") for source in sources(entity) if source.get("status") == "supporting"]
    tasks = {task for task in task_ids if isinstance(task, (str, int)) and task}
    complete = all(isinstance(task, (str, int)) and task for task in task_ids) and not (entity.metadata or {}).get("provenance_incomplete")
    if tasks and complete:
        return len(tasks)
    return max(len(tasks), stored_support(entity))


def _source_tasks(entity: RecordedEntity) -> list[str]:
    """The source tasks a candidate is ranked by under ``"source_task"``.

    A guideline merged across tasks carries all of them in ``source_tasks``, and any
    guideline may have a ``task_description``; one with neither is ranked by its own content.
    """
    return stored_tasks(entity) or [str(entity.content)]


def _embed(texts: list[str], embedding_model: str) -> np.ndarray:
    model = _get_sentence_transformer(embedding_model)
    return np.asarray(model.encode(texts, normalize_embeddings=True))


def select_guidelines(
    entities: list[RecordedEntity],
    task_query: str,
    *,
    top_k: int = 10,
    core_support: int = 3,
    min_support: int = 1,
    similarity_key: SimilarityKey = "source_task",
    near_core_thresh: float = 0.75,
    dedup_thresh: float = 0.90,
    embedding_model: str | None = None,
) -> GuidelineSelection:
    """Select the always-on core plus the top-``top_k`` task-relevant guidelines.

    Args:
        entities: Candidate guideline entities (carrying ``support`` and, for
            ``similarity_key="source_task"``, ``task_description`` in metadata).
        task_query: The current task instruction to retrieve for.
        top_k: Maximum number of retrieved (non-core) guidelines.
        core_support: Guidelines backed by >= this many distinct source tasks are always included.
        min_support: Non-destructive sup2/sup3 floor applied to the whole pool *before* the
            core/candidate split. Callers should keep ``min_support <= core_support`` (enforced
            by ``EvolveConfig``) so the floor never drops a guideline that qualifies for the core.
        similarity_key: Retrieve by the source ``task_description`` (default) or by the
            guideline text itself. For ``"source_task"``, a candidate merged across tasks
            scores as the best match among its ``source_tasks``, and a candidate lacking a
            ``task_description`` falls back to being ranked by its own content.
        near_core_thresh: Drop a candidate whose content cosine to any core guideline is
            >= this (already covered by the core).
        dedup_thresh: Drop a candidate whose content cosine to an already-kept candidate is
            >= this.
        embedding_model: SentenceTransformer model name. Defaults to the configured model.

    Returns:
        A :class:`GuidelineSelection` (core first, then retrieved by descending relevance).
    """
    if embedding_model is None:
        from altk_evolve.config.milvus import milvus_other_settings

        embedding_model = milvus_other_settings.embedding_model

    pool = [e for e in entities if _support(e) >= min_support]
    core = [e for e in pool if _support(e) >= core_support]
    candidates = [e for e in pool if _support(e) < core_support]

    if top_k <= 0 or not candidates:
        return GuidelineSelection(core=core, retrieved=[])

    core_content_emb = _embed([str(e.content) for e in core], embedding_model) if core else None
    cand_content_emb = _embed([str(e.content) for e in candidates], embedding_model)
    query_emb = _embed([task_query], embedding_model)[0]
    if similarity_key == "guideline_text":
        relevance = cand_content_emb @ query_emb
    else:
        # A candidate with several source tasks scores as its best-matching one.
        tasks = [_source_tasks(e) for e in candidates]
        task_scores = _embed([task for ts in tasks for task in ts], embedding_model) @ query_emb
        bounds = np.cumsum([0, *(len(ts) for ts in tasks)])
        relevance = np.array([task_scores[start:end].max() for start, end in zip(bounds[:-1], bounds[1:])])

    # Drop candidates already covered by the core, then dedup the remainder, then rank by
    # similarity of the source task to the current task.
    kept: list[tuple[int, float]] = []  # (candidate index, relevance score)
    kept_content: list[np.ndarray] = []
    order = sorted(range(len(candidates)), key=lambda i: float(relevance[i]), reverse=True)
    for i in order:
        content_vec = cand_content_emb[i]
        if core_content_emb is not None and float(np.max(core_content_emb @ content_vec)) >= near_core_thresh:
            continue
        if kept_content and float(np.max(np.stack(kept_content) @ content_vec)) >= dedup_thresh:
            continue
        kept.append((i, float(relevance[i])))
        kept_content.append(content_vec)
        if len(kept) >= top_k:
            break

    retrieved = [candidates[i] for i, _ in kept]
    return GuidelineSelection(core=core, retrieved=retrieved)


def format_selection(selection: GuidelineSelection) -> str:
    """Render a selection as an injectable guidelines block (mirrors the retrieved-tips agent)."""
    lines = [
        "Guidelines learned from past task attempts (follow these carefully; they address the most common mistakes):",
        "",
    ]
    for entity in selection.core:
        lines.append(f"- {entity.content}")
    if selection.retrieved:
        lines.append("")
        lines.append("Additional task-specific guidelines retrieved from similar past tasks:")
        for entity in selection.retrieved:
            lines.append(f"- {entity.content}")
    return "\n".join(lines)
