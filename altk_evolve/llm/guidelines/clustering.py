"""Cluster guideline entities by task description similarity."""

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

from altk_evolve.config.evolve import evolve_config
from altk_evolve.config.llm import llm_settings
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.schema.guidelines import ConsolidatedGuideline, ConsolidatedGuidelineResponse, Evidence, Guideline
from altk_evolve.utils.utils import clean_llm_response

logger = logging.getLogger(__name__)

MAX_CLUSTER_ENTITIES = 5000
_VALID_CATEGORIES = {"strategy", "recovery", "optimization"}

_COMBINE_GUIDELINES_TEMPLATE = Template((Path(__file__).parent / "prompts/combine_guidelines.jinja2").read_text())


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
            configured in ``evolve.config.milvus``.

    Returns:
        List of clusters (each a list of ``RecordedEntity``), excluding
        single-entity clusters.
    """
    if embedding_model is None:
        from altk_evolve.config.milvus import milvus_other_settings

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


def _normalize_steps(raw: object) -> list[str]:
    if raw is None or raw == []:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return [str(raw)]


def _merge_evidence(evidences: list[Evidence | None]) -> Evidence | None:
    """Merge evidence markers: any success+failure (or an existing 'both') -> 'both'."""
    present = {e for e in evidences if e}
    if not present:
        return None
    if "both" in present or ("success" in present and "failure" in present):
        return "both"
    return "success" if "success" in present else "failure"


def _coerce_category(value: object) -> str:
    return str(value) if value in _VALID_CATEGORIES else "strategy"


def _attribute_support(
    entities: list[RecordedEntity],
    consolidated: list[ConsolidatedGuideline],
    member_support: list[int],
    member_evidence: list[Evidence | None],
) -> list[Guideline]:
    """Map consolidated guidelines back to their source members, conserving total support.

    Each input index is attributed to exactly one output guideline (the first that claims
    it via ``source_indices``). Any index the model failed to cover is carried through
    unchanged as its own guideline, so no advice is ever dropped and ``sum(support)`` is
    preserved.
    """
    n = len(entities)
    assigned = [False] * n
    out: list[Guideline] = []

    for cg in consolidated:
        # Dedupe within a single guideline's source_indices so a repeated index (e.g.
        # [0, 0, 1]) can't double-count its member's support.
        seen: set[int] = set()
        idxs: list[int] = []
        for i in cg.source_indices:
            if isinstance(i, int) and 0 <= i < n and not assigned[i] and i not in seen:
                seen.add(i)
                idxs.append(i)
        if not idxs:
            continue
        for i in idxs:
            assigned[i] = True
        out.append(
            Guideline(
                content=cg.content,
                rationale=cg.rationale,
                category=cg.category,
                trigger=cg.trigger,
                implementation_steps=cg.implementation_steps,
                support=sum(member_support[i] for i in idxs),
                evidence=_merge_evidence([member_evidence[i] for i in idxs]),
            )
        )

    # Fail-safe: any uncovered member survives unchanged as its own guideline (lossless).
    for i in range(n):
        if assigned[i]:
            continue
        md = entities[i].metadata or {}
        out.append(
            Guideline(
                content=str(entities[i].content),
                rationale=str(md.get("rationale", "")),
                category=_coerce_category(md.get("category")),  # type: ignore[arg-type]
                trigger=str(md.get("trigger", "")),
                implementation_steps=_normalize_steps(md.get("implementation_steps")),
                support=member_support[i],
                evidence=member_evidence[i],
            )
        )

    total_in, total_out = sum(member_support), sum(g.support for g in out)
    if total_out != total_in:
        logger.warning("Support not conserved during consolidation (in=%d, out=%d).", total_in, total_out)
    return out


def combine_cluster(entities: list[RecordedEntity], mode: str = "lossless") -> list[Guideline]:
    """Combine guidelines from a cluster of related entities into consolidated guidelines.

    Uses an LLM to merge related guidelines while conserving each guideline's ``support``
    count (number of source guidelines behind it) and merging ``evidence`` polarity.

    Args:
        entities: Cluster of related entities to combine.
        mode: Merge style — ``"lossless"`` (merge only equivalent advice; default) or
            ``"lossy"`` (merge more aggressively). Support is conserved either way; no
            advice is dropped in this step.

    Returns:
        Consolidated list of guidelines with ``support``/``evidence`` populated.

    Raises:
        EvolveException: If the LLM call fails after 3 attempts.
    """
    is_groq = llm_settings.custom_llm_provider == "groq" or llm_settings.guidelines_model.startswith("groq/")
    supported_params = get_supported_openai_params(
        model=llm_settings.guidelines_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    supports_response_format = bool(supported_params and "response_format" in supported_params)
    response_schema_enabled = supports_response_schema(
        model=llm_settings.guidelines_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    constrained_decoding_supported = not is_groq and supports_response_format and response_schema_enabled

    # Deduplicate task descriptions
    task_descriptions = list(
        dict.fromkeys((e.metadata or {}).get("task_description", "") for e in entities if (e.metadata or {}).get("task_description"))
    )

    # Per-member support/evidence carried in metadata (defaults: support 1, evidence unknown).
    member_support = [max(1, int((e.metadata or {}).get("support", 1) or 1)) for e in entities]
    member_evidence: list[Evidence | None] = [(e.metadata or {}).get("evidence") for e in entities]

    guidelines = [
        {
            "content": str(e.content),
            "rationale": (e.metadata or {}).get("rationale", ""),
            "category": (e.metadata or {}).get("category", "strategy"),
            "trigger": (e.metadata or {}).get("trigger", ""),
            "implementation_steps": _normalize_steps((e.metadata or {}).get("implementation_steps")),
        }
        for e in entities
    ]

    prompt = _COMBINE_GUIDELINES_TEMPLATE.render(
        task_descriptions=task_descriptions,
        guidelines=guidelines,
        constrained_decoding_supported=constrained_decoding_supported,
        merge_style="aggressive" if mode == "lossy" else "conservative",
        target_count=evolve_config.lossy_target_num_guidelines,
    )

    litellm.enable_json_schema_validation = constrained_decoding_supported

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if constrained_decoding_supported:
                content = (
                    completion(
                        model=llm_settings.guidelines_model,
                        messages=[{"role": "user", "content": prompt}],
                        response_format=ConsolidatedGuidelineResponse,
                        custom_llm_provider=llm_settings.custom_llm_provider,
                    )
                    .choices[0]
                    .message.content
                )
                if content is None:
                    raise EvolveException("LLM returned None content for combine_cluster")
                clean_response = content
            else:
                content = (
                    completion(
                        model=llm_settings.guidelines_model,
                        messages=[{"role": "user", "content": prompt}],
                        custom_llm_provider=llm_settings.custom_llm_provider,
                    )
                    .choices[0]
                    .message.content
                )
                if content is None:
                    raise EvolveException("LLM returned None content for combine_cluster")
                clean_response = clean_llm_response(content)

            consolidated = ConsolidatedGuidelineResponse.model_validate(json.loads(clean_response)).guidelines
            return _attribute_support(entities, consolidated, member_support, member_evidence)
        except Exception as e:
            last_error = e
            if attempt < 2:
                continue

    raise EvolveException("Failed to combine cluster guidelines after 3 attempts") from last_error
