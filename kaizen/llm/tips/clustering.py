"""Cluster tip entities by task description similarity."""

from __future__ import annotations

import numpy as np
from sentence_transformers import SentenceTransformer

from kaizen.schema.core import RecordedEntity


def _union_find(n: int, pairs: list[tuple[int, int]]) -> list[list[int]]:
    """Group indices into connected components using union-find with path compression.

    Args:
        n: Total number of elements.
        pairs: Index pairs (i, j) to union together.

    Returns:
        List of groups, where each group is a list of indices.
    """
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(i)

    return list(groups.values())


def cluster_entities(
    entities: list[RecordedEntity],
    threshold: float = 0.80,
    embedding_model: str | None = None,
) -> list[list[RecordedEntity]]:
    """Cluster entities by cosine similarity of their task descriptions.

    Args:
        entities: Guideline entities with optional ``task_description`` in metadata.
        threshold: Cosine similarity threshold for clustering (0-1).
        embedding_model: SentenceTransformer model name. Defaults to the model
            configured in ``kaizen.config.milvus``.

    Returns:
        List of clusters (each a list of ``RecordedEntity``), excluding
        single-entity clusters.
    """
    if embedding_model is None:
        from kaizen.config.milvus import milvus_other_settings

        embedding_model = milvus_other_settings.embedding_model

    # Filter to entities that have a task_description
    filtered: list[tuple[int, RecordedEntity]] = []
    for idx, entity in enumerate(entities):
        td = (entity.metadata or {}).get("task_description")
        if td:
            filtered.append((idx, entity))

    if len(filtered) < 2:
        return []

    descriptions = [e.metadata["task_description"] for _, e in filtered]

    model = SentenceTransformer(embedding_model)
    embeddings = model.encode(descriptions, normalize_embeddings=True)
    similarity_matrix = np.asarray(embeddings) @ np.asarray(embeddings).T

    # Find pairs exceeding threshold
    n = len(filtered)
    pairs: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if similarity_matrix[i, j] > threshold:
                pairs.append((i, j))

    groups = _union_find(n, pairs)

    # Convert index groups back to entity clusters, excluding singletons
    clusters: list[list[RecordedEntity]] = []
    for group in groups:
        if len(group) < 2:
            continue
        clusters.append([filtered[i][1] for i in group])

    return clusters
