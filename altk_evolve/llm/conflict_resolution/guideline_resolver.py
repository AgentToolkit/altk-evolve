"""Guideline-specific conflict resolution (Phase 2).

The base resolver in `conflict_resolution.py` is type-agnostic. Guidelines
benefit from a per-track resolver that:

1. Surfaces `confidence_weighted_score` (probability-of-success in [0, 1])
   to the LLM alongside `category` and `trigger`, so the model can prefer
   higher-scored guidelines on contradiction.
2. Applies a mechanical **diff-size guardrail** on UPDATE events: when the
   LLM proposes replacing an old guideline with content that differs in
   length by more than 50%, the UPDATE is downgraded to NONE. This prevents
   hallucinated rewrites from silently destroying curated content
   (per implementation_plan.md §13).
3. Re-attaches metadata for ADD events (mirrors base resolver behavior).

All `completion` calls go through the parent `conflict_resolution` module's
namespace so existing test mocks continue to work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Template
from pydantic import BaseModel, Field

from altk_evolve.config.llm import llm_settings
from altk_evolve.llm.conflict_resolution import conflict_resolution as _base
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.utils.utils import clean_llm_response


# ── tunables ──────────────────────────────────────────────────────────────

# Reject UPDATEs whose content size differs from the original by more than
# this fraction. Hallucinated rewrites tend to be much shorter or much
# longer than the source guideline.
_DIFF_RATIO_REJECT = 0.5

# Default cold-start score when an entity has no outcome_evidence (matches
# COLD_START_PRIOR_LLM_EXTRACTED in schema/outcome_evidence.py).
_DEFAULT_SCORE = 0.5


# ── prompt input schema ───────────────────────────────────────────────────


class _GuidelineSimpleEntity(BaseModel):
    """LLM-input shape for guideline conflict resolution.

    Mirrors `SimpleEntity` but exposes the three guideline-specific signals
    the prompt reasons about: category, trigger, and outcome score.
    """

    id: str
    type: str
    content: str | list | dict
    category: str = ""
    trigger: str = Field(default="", description="Situation that activates the guideline.")
    score: float = Field(default=_DEFAULT_SCORE, ge=0.0, le=1.0)

    @staticmethod
    def from_recorded_entities(entities: list[RecordedEntity]) -> list[_GuidelineSimpleEntity]:
        out: list[_GuidelineSimpleEntity] = []
        for e in entities:
            meta = e.metadata or {}
            evidence = meta.get("outcome_evidence") or {}
            aggregated = (evidence or {}).get("aggregated") if isinstance(evidence, dict) else None
            score = _DEFAULT_SCORE
            if isinstance(aggregated, dict):
                raw_score = aggregated.get("confidence_weighted_score")
                if isinstance(raw_score, (int, float)):
                    score = max(0.0, min(1.0, float(raw_score)))
            out.append(
                _GuidelineSimpleEntity(
                    id=e.id,
                    type=e.type,
                    content=e.content,
                    category=str(meta.get("category", "")),
                    trigger=str(meta.get("trigger", "")),
                    score=score,
                )
            )
        return out


# ── public entrypoint ─────────────────────────────────────────────────────


def resolve_guideline_conflicts(
    old_entities: list[RecordedEntity],
    new_entities: list[RecordedEntity],
) -> list[EntityUpdate]:
    """Per-track conflict resolution for guidelines.

    Drop-in replacement for `resolve_conflicts` when both sides are
    guideline-typed. Caller (the dispatch in `conflict_resolution.py`)
    is responsible for routing.
    """
    simple_old = _GuidelineSimpleEntity.from_recorded_entities(old_entities)
    simple_new = _GuidelineSimpleEntity.from_recorded_entities(new_entities)
    new_by_id = {e.id: e for e in new_entities}
    old_by_id = {e.id: e for e in old_entities}

    prompt = _build_prompt(simple_old, simple_new)

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            # Use the parent module's `completion` symbol so test patches
            # at `conflict_resolution.completion` apply here too.
            response = _base.completion(
                model=llm_settings.conflict_resolution_model,
                messages=[{"role": "user", "content": prompt}],
                custom_llm_provider=llm_settings.custom_llm_provider,
            )
            text = clean_llm_response(response.choices[0].message.content or "")  # type: ignore[union-attr]
            parsed = json.loads(text)
            updates = [EntityUpdate.model_validate(e) for e in parsed["entities"]]
            updates = _apply_diff_size_guard(updates, old_by_id)
            for update in updates:
                if update.event == "ADD" and update.id in new_by_id:
                    update.metadata = new_by_id[update.id].metadata
            return updates
        except Exception as e:  # noqa: BLE001
            last_error = e
            if attempt < 2:
                continue
    raise EvolveException("Failed to resolve conflicts after 3 attempts") from last_error


# ── internals ─────────────────────────────────────────────────────────────


def _build_prompt(old: list[_GuidelineSimpleEntity], new: list[_GuidelineSimpleEntity]) -> str:
    inner_path = Path(__file__).parent / "prompts/default_guideline_conflict_resolution.jinja2"
    inner = Template(inner_path.read_text()).render()
    outer_path = Path(__file__).parent / "prompts/conflict_resolution.jinja2"
    return Template(outer_path.read_text()).render(
        custom_update_entities_prompt=inner,
        old_entities=json.dumps([e.model_dump(mode="json") for e in old], indent=4),
        new_entities=json.dumps([e.model_dump(mode="json") for e in new], indent=4),
    )


def _apply_diff_size_guard(
    updates: list[EntityUpdate],
    old_by_id: dict[str, RecordedEntity],
) -> list[EntityUpdate]:
    """Downgrade UPDATEs whose new content size diverges too far from old.

    Hallucinated rewrites tend to either compress the original to a sentence
    or expand it into a multi-paragraph essay. Either case loses information
    we already have.
    """
    out: list[EntityUpdate] = []
    for update in updates:
        if update.event != "UPDATE" or update.id not in old_by_id:
            out.append(update)
            continue
        old_content = _content_str(old_by_id[update.id].content)
        new_content = _content_str(update.content)
        if not old_content:
            out.append(update)
            continue
        diff_ratio = abs(len(new_content) - len(old_content)) / len(old_content)
        if diff_ratio > _DIFF_RATIO_REJECT:
            # Downgrade: keep the old guideline as-is.
            update.event = "NONE"
            update.content = old_by_id[update.id].content
            update.old_entity = None
        out.append(update)
    return out


def _content_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, sort_keys=True) if content is not None else ""
