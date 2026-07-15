"""Dosage-aware guideline selection: always-on core + per-task retrieved guidelines.

Instead of injecting the whole playbook every task (best only for strong models), this
selects a small, task-relevant dose (best for weaker models — the capability-dependent
dosage finding):

    core      = guidelines whose ``support`` >= ``core_support`` (they recurred across many
                tasks, so they generalise) — always included.
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

from altk_evolve.llm.guidelines.clustering import _get_sentence_transformer
from altk_evolve.schema.core import RecordedEntity

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
    try:
        return max(1, int((entity.metadata or {}).get("support", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _key_text(entity: RecordedEntity, similarity_key: SimilarityKey) -> str:
    if similarity_key == "source_task":
        return str((entity.metadata or {}).get("task_description", "") or entity.content)
    return str(entity.content)


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
        core_support: Guidelines with support >= this are always included.
        min_support: Non-destructive sup2/sup3 floor applied to the whole pool *before* the
            core/candidate split. Callers should keep ``min_support <= core_support`` (enforced
            by ``EvolveConfig``) so the floor never drops a guideline that qualifies for the core.
        similarity_key: Retrieve by the source ``task_description`` (default) or by the
            guideline text itself. For ``"source_task"``, a candidate lacking a
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
    cand_key_emb = (
        cand_content_emb
        if similarity_key == "guideline_text"
        else _embed([_key_text(e, similarity_key) for e in candidates], embedding_model)
    )
    query_emb = _embed([task_query], embedding_model)[0]

    # Drop candidates already covered by the core, then dedup the remainder, then rank by
    # similarity of the source task to the current task.
    kept: list[tuple[int, float]] = []  # (candidate index, relevance score)
    kept_content: list[np.ndarray] = []
    order = sorted(range(len(candidates)), key=lambda i: float(cand_key_emb[i] @ query_emb), reverse=True)
    for i in order:
        content_vec = cand_content_emb[i]
        if core_content_emb is not None and float(np.max(core_content_emb @ content_vec)) >= near_core_thresh:
            continue
        if kept_content and float(np.max(np.stack(kept_content) @ content_vec)) >= dedup_thresh:
            continue
        kept.append((i, float(cand_key_emb[i] @ query_emb)))
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
