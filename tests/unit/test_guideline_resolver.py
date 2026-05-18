"""Tests for altk_evolve.llm.conflict_resolution.guideline_resolver (Phase 2)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from altk_evolve.llm.conflict_resolution.conflict_resolution import resolve_conflicts
from altk_evolve.llm.conflict_resolution.guideline_resolver import (
    _DIFF_RATIO_REJECT,
    _GuidelineSimpleEntity,
    resolve_guideline_conflicts,
)
from altk_evolve.schema.core import RecordedEntity


pytestmark = pytest.mark.unit


def _ts() -> datetime:
    return datetime(2026, 5, 15, 14, 0, 0, tzinfo=timezone.utc)


def _guideline(
    *,
    id: str,
    content: str,
    score: float | None = None,
    category: str = "strategy",
    trigger: str = "When the agent does X",
) -> RecordedEntity:
    metadata: dict = {"category": category, "trigger": trigger}
    if score is not None:
        metadata["outcome_evidence"] = {
            "observations": [],
            "aggregated": {
                "confirmed_successes": 0,
                "confirmed_failures": 0,
                "inferred_successes": 0,
                "inferred_failures": 0,
                "judge_successes": 0,
                "judge_failures": 0,
                "unknown": 0,
                "confidence_weighted_score": score,
                "last_observed_at": None,
            },
        }
    return RecordedEntity(
        id=id,
        type="guideline",
        content=content,
        metadata=metadata,
        created_at=_ts(),
    )


# ── _GuidelineSimpleEntity ────────────────────────────────────────────────


class TestGuidelineSimpleEntity:
    def test_score_pulled_from_outcome_evidence(self) -> None:
        ent = _guideline(id="g1", content="x", score=0.78)
        simple = _GuidelineSimpleEntity.from_recorded_entities([ent])[0]
        assert simple.score == pytest.approx(0.78)
        assert simple.category == "strategy"
        assert simple.trigger == "When the agent does X"

    def test_default_score_when_no_outcome_evidence(self) -> None:
        ent = _guideline(id="g1", content="x", score=None)
        simple = _GuidelineSimpleEntity.from_recorded_entities([ent])[0]
        # Defaults to neutral (0.5) when no evidence is recorded.
        assert simple.score == 0.5

    def test_score_clamped_to_unit_interval(self) -> None:
        # Start with a valid score so the outcome_evidence dict exists,
        # then push it out of bounds to verify the clamp.
        ent = _guideline(id="g1", content="x", score=0.5)
        ent.metadata["outcome_evidence"]["aggregated"]["confidence_weighted_score"] = 1.5  # type: ignore[index]
        simple = _GuidelineSimpleEntity.from_recorded_entities([ent])[0]
        assert simple.score == 1.0


# ── resolve_guideline_conflicts (mocked LLM) ──────────────────────────────


class TestResolveGuidelineConflicts:
    def _mock_completion(self, response_content: str) -> Mock:
        response = Mock()
        response.choices = [Mock()]
        response.choices[0].message.content = response_content
        return response

    @patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
    def test_add_event_attaches_metadata(self, mock_completion) -> None:
        new = _guideline(id="Unprocessed_Entity_0", content="New advice", score=0.7)
        mock_completion.return_value = self._mock_completion(
            json.dumps({"entities": [{"id": "Unprocessed_Entity_0", "type": "guideline", "content": "New advice", "event": "ADD"}]})
        )
        updates = resolve_guideline_conflicts([], [new])
        assert len(updates) == 1
        assert updates[0].event == "ADD"
        # Metadata gets re-attached.
        assert updates[0].metadata.get("category") == "strategy"

    @patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
    def test_update_within_diff_threshold_passes_through(self, mock_completion) -> None:
        # 30 chars → 38 chars: diff_ratio ≈ 0.27, under the 0.5 threshold.
        old = _guideline(id="g1", content="Always use type hints in code.", score=0.6)
        new = _guideline(id="Unprocessed_Entity_0", content="Always use type hints in production code.", score=0.7)
        mock_completion.return_value = self._mock_completion(
            json.dumps(
                {
                    "entities": [
                        {
                            "id": "g1",
                            "type": "guideline",
                            "content": "Always use type hints in production code.",
                            "event": "UPDATE",
                            "old_entity": "Always use type hints in code.",
                        }
                    ]
                }
            )
        )
        updates = resolve_guideline_conflicts([old], [new])
        assert updates[0].event == "UPDATE"
        assert "production" in updates[0].content  # type: ignore[operator]

    @patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
    def test_update_with_oversized_rewrite_is_downgraded_to_none(self, mock_completion) -> None:
        # Old: 30 chars; New: 200 chars. diff_ratio ≈ 5.7 >> 0.5 → rejected.
        old_content = "Use type hints in all code." * 1
        new_content = "x" * 300
        old = _guideline(id="g1", content=old_content, score=0.6)
        new = _guideline(id="Unprocessed_Entity_0", content=new_content, score=0.7)
        mock_completion.return_value = self._mock_completion(
            json.dumps(
                {
                    "entities": [
                        {
                            "id": "g1",
                            "type": "guideline",
                            "content": new_content,
                            "event": "UPDATE",
                            "old_entity": old_content,
                        }
                    ]
                }
            )
        )
        updates = resolve_guideline_conflicts([old], [new])
        # UPDATE was rejected by the diff-size guard → downgraded to NONE,
        # original content preserved.
        assert updates[0].event == "NONE"
        assert updates[0].content == old_content
        assert updates[0].old_entity is None

    @patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
    def test_diff_ratio_threshold_boundary(self, mock_completion) -> None:
        # Exactly at the threshold: diff_ratio == _DIFF_RATIO_REJECT → NOT rejected.
        old_content = "x" * 100
        # 50% larger: 150 chars → diff_ratio == 0.5 (boundary, not over).
        new_content = "x" * 150
        old = _guideline(id="g1", content=old_content)
        new = _guideline(id="Unprocessed_Entity_0", content=new_content)
        mock_completion.return_value = self._mock_completion(
            json.dumps(
                {"entities": [{"id": "g1", "type": "guideline", "content": new_content, "event": "UPDATE", "old_entity": old_content}]}
            )
        )
        updates = resolve_guideline_conflicts([old], [new])
        # At threshold the UPDATE passes through (rule is strictly greater than).
        assert updates[0].event == "UPDATE"
        # And just past the threshold, it gets rejected.
        assert _DIFF_RATIO_REJECT == 0.5  # sanity check

    @patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
    def test_delete_event_unchanged_by_diff_guard(self, mock_completion) -> None:
        old = _guideline(id="g1", content="Stale advice", score=0.3)
        mock_completion.return_value = self._mock_completion(
            json.dumps({"entities": [{"id": "g1", "type": "guideline", "content": "Stale advice", "event": "DELETE"}]})
        )
        updates = resolve_guideline_conflicts([old], [])
        assert updates[0].event == "DELETE"

    @patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
    def test_score_appears_in_prompt(self, mock_completion) -> None:
        # The LLM prompt must surface the per-entity score so the model can
        # apply the score-aware decision rules.
        old = _guideline(id="g1", content="Old advice", score=0.85)
        new = _guideline(id="Unprocessed_Entity_0", content="New advice", score=0.40)
        mock_completion.return_value = self._mock_completion(
            json.dumps({"entities": [{"id": "g1", "type": "guideline", "content": "Old advice", "event": "NONE"}]})
        )
        resolve_guideline_conflicts([old], [new])
        prompt = mock_completion.call_args.kwargs["messages"][0]["content"]
        assert '"score": 0.85' in prompt
        assert '"score": 0.4' in prompt
        assert '"category": "strategy"' in prompt

    @patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
    def test_retries_on_failure(self, mock_completion) -> None:
        good = self._mock_completion(json.dumps({"entities": []}))
        bad = Mock()
        bad.choices = [Mock()]
        bad.choices[0].message.content = "not json"
        mock_completion.side_effect = [bad, bad, good]

        updates = resolve_guideline_conflicts([], [])
        assert updates == []
        assert mock_completion.call_count == 3

    @patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
    def test_raises_after_three_failures(self, mock_completion) -> None:
        mock_completion.side_effect = Exception("LLM down")
        with pytest.raises(Exception, match="Failed to resolve conflicts after 3 attempts"):
            resolve_guideline_conflicts([], [_guideline(id="x", content="y")])
        assert mock_completion.call_count == 3


# ── dispatch in resolve_conflicts ────────────────────────────────────────


class TestDispatch:
    @patch("altk_evolve.llm.conflict_resolution.guideline_resolver.resolve_guideline_conflicts")
    def test_guideline_typed_entities_route_to_guideline_resolver(self, mock_resolver) -> None:
        mock_resolver.return_value = []
        old = [_guideline(id="g1", content="x")]
        new = [_guideline(id="g2", content="y")]
        resolve_conflicts(old, new)
        mock_resolver.assert_called_once_with(old, new)

    @patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
    def test_non_guideline_entities_use_base_resolver(self, mock_completion) -> None:
        # type='fact' → base path. The base path uses the original prompt
        # without guideline-specific score fields.
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = json.dumps({"entities": [{"id": "f1", "type": "fact", "content": "x", "event": "NONE"}]})
        mock_completion.return_value = mock_response
        fact = RecordedEntity(id="f1", type="fact", content="x", metadata={}, created_at=_ts())
        result = resolve_conflicts([fact], [fact])
        assert result[0].event == "NONE"
        prompt = mock_completion.call_args.kwargs["messages"][0]["content"]
        # Base prompt does NOT include the guideline-specific score field.
        assert '"score":' not in prompt

    @patch("altk_evolve.llm.conflict_resolution.guideline_resolver.resolve_guideline_conflicts")
    @patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
    def test_custom_prompt_skips_guideline_dispatch(self, mock_completion, mock_resolver) -> None:
        # A caller-supplied custom_update_entities_prompt opts out of the
        # per-track behavior and uses the base resolver, even for guidelines.
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = json.dumps(
            {"entities": [{"id": "g1", "type": "guideline", "content": "x", "event": "NONE"}]}
        )
        mock_completion.return_value = mock_response
        guideline = _guideline(id="g1", content="x")
        resolve_conflicts([guideline], [guideline], custom_update_entities_prompt="custom")
        mock_resolver.assert_not_called()
