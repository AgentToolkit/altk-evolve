"""Cluster tip entities by task description similarity."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

import litellm
import numpy as np
from jinja2 import Template
from litellm import completion, get_supported_openai_params, supports_response_schema
from sentence_transformers import SentenceTransformer

from kaizen.config.llm import llm_settings
from kaizen.schema.core import RecordedEntity
from kaizen.schema.exceptions import KaizenException
from kaizen.schema.tips import Tip, TipGenerationResponse
from kaizen.utils.utils import clean_llm_response

logger = logging.getLogger(__name__)

MAX_CLUSTER_ENTITIES = 5000

_COMBINE_TIPS_TEMPLATE = Template((Path(__file__).parent / "prompts/combine_tips.jinja2").read_text())


@lru_cache(maxsize=4)
def _get_sentence_transformer(model_name: str) -> SentenceTransformer:
    return SentenceTransformer(model_name)


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

    if len(filtered) > MAX_CLUSTER_ENTITIES:
        logger.warning(
            "Too many entities for clustering (%d > %d). Truncating to first %d.",
            len(filtered),
            MAX_CLUSTER_ENTITIES,
            MAX_CLUSTER_ENTITIES,
        )
        filtered = filtered[:MAX_CLUSTER_ENTITIES]

    descriptions = [e.metadata["task_description"] for _, e in filtered]

    model = _get_sentence_transformer(embedding_model)
    embeddings = model.encode(descriptions, normalize_embeddings=True)
    similarity_matrix = np.asarray(embeddings) @ np.asarray(embeddings).T

    # Find pairs meeting threshold (vectorized upper-triangle extraction)
    n = len(filtered)
    mask = np.triu(similarity_matrix >= threshold, k=1)
    rows, cols = np.where(mask)
    pairs: list[tuple[int, int]] = list(zip(rows.tolist(), cols.tolist()))

    groups = _union_find(n, pairs)

    # Convert index groups back to entity clusters, excluding singletons
    clusters: list[list[RecordedEntity]] = []
    for group in groups:
        if len(group) < 2:
            continue
        clusters.append([filtered[i][1] for i in group])

    return clusters


def combine_cluster(entities: list[RecordedEntity]) -> list[Tip]:
    """Combine tips from a cluster of related entities into consolidated guidelines.

    Uses an LLM to merge overlapping tips into fewer, non-redundant guidelines.

    Args:
        entities: Cluster of related entities to combine.

    Returns:
        Consolidated list of tips.

    Raises:
        KaizenException: If the LLM call fails after 3 attempts.
    """
    supported_params = get_supported_openai_params(
        model=llm_settings.tips_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    supports_response_format = bool(supported_params and "response_format" in supported_params)
    response_schema_enabled = supports_response_schema(
        model=llm_settings.tips_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    constrained_decoding_supported = supports_response_format and response_schema_enabled

    # Deduplicate task descriptions
    task_descriptions = list(
        dict.fromkeys((e.metadata or {}).get("task_description", "") for e in entities if (e.metadata or {}).get("task_description"))
    )

    tips = [
        {
            "content": str(e.content),
            "rationale": (e.metadata or {}).get("rationale", ""),
            "category": (e.metadata or {}).get("category", "strategy"),
            "trigger": (e.metadata or {}).get("trigger", ""),
        }
        for e in entities
    ]

    prompt = _COMBINE_TIPS_TEMPLATE.render(
        task_descriptions=task_descriptions,
        tips=tips,
        constrained_decoding_supported=constrained_decoding_supported,
    )

    litellm.enable_json_schema_validation = constrained_decoding_supported

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if constrained_decoding_supported:
                content = (
                    completion(
                        model=llm_settings.tips_model,
                        messages=[{"role": "user", "content": prompt}],
                        response_format=TipGenerationResponse,
                        custom_llm_provider=llm_settings.custom_llm_provider,
                    )
                    .choices[0]
                    .message.content
                )
                if content is None:
                    raise KaizenException("LLM returned None content for combine_cluster")
                clean_response = content
            else:
                content = (
                    completion(
                        model=llm_settings.tips_model,
                        messages=[{"role": "user", "content": prompt}],
                        custom_llm_provider=llm_settings.custom_llm_provider,
                    )
                    .choices[0]
                    .message.content
                )
                if content is None:
                    raise KaizenException("LLM returned None content for combine_cluster")
                clean_response = clean_llm_response(content)

            return TipGenerationResponse.model_validate(json.loads(clean_response)).tips
        except Exception as e:
            last_error = e
            if attempt < 2:
                continue

    raise KaizenException("Failed to combine cluster tips after 3 attempts") from last_error
