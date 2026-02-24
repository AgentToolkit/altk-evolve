"""Unit tests for tip combining and consolidation logic."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from kaizen.llm.tips.clustering import combine_cluster
from kaizen.schema.core import RecordedEntity
from kaizen.schema.exceptions import KaizenException
from kaizen.schema.tips import Tip, ConsolidationResult


def _make_entity(entity_id: str, content: str, task_description: str = "do a task") -> RecordedEntity:
    return RecordedEntity(
        id=entity_id,
        content=content,
        type="guideline",
        metadata={
            "task_description": task_description,
            "rationale": "some rationale",
            "category": "strategy",
            "trigger": "when needed",
        },
        created_at=datetime(2025, 1, 1),
    )


def _mock_completion_response(tips: list[dict]) -> MagicMock:
    """Build a mock litellm completion response."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps({"tips": tips})
    return response


SAMPLE_TIPS = [
    {
        "content": "Use retry logic for flaky APIs",
        "rationale": "APIs can fail transiently",
        "category": "recovery",
        "trigger": "When calling external APIs",
    },
    {
        "content": "Log errors with context",
        "rationale": "Easier debugging",
        "category": "optimization",
        "trigger": "When handling exceptions",
    },
]


# ---------------------------------------------------------------------------
# combine_cluster tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCombineCluster:
    @patch("kaizen.llm.tips.clustering.completion")
    @patch("kaizen.llm.tips.clustering.supports_response_schema", return_value=False)
    @patch("kaizen.llm.tips.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_returns_tips(self, _mock_params, _mock_schema, mock_completion):
        mock_completion.return_value = _mock_completion_response(SAMPLE_TIPS)

        entities = [
            _make_entity("1", "Always retry on failure"),
            _make_entity("2", "Add error logging"),
        ]

        result = combine_cluster(entities)

        assert len(result) == 2
        assert all(isinstance(t, Tip) for t in result)
        assert result[0].content == "Use retry logic for flaky APIs"
        assert result[1].category == "optimization"
        mock_completion.assert_called_once()

    @patch("kaizen.llm.tips.clustering.completion")
    @patch("kaizen.llm.tips.clustering.supports_response_schema", return_value=False)
    @patch("kaizen.llm.tips.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_retries_on_failure(self, _mock_params, _mock_schema, mock_completion):
        mock_completion.side_effect = [
            ValueError("bad json"),
            ValueError("bad json again"),
            _mock_completion_response(SAMPLE_TIPS[:1]),
        ]

        entities = [_make_entity("1", "Tip A"), _make_entity("2", "Tip B")]
        result = combine_cluster(entities)

        assert len(result) == 1
        assert result[0].content == "Use retry logic for flaky APIs"
        assert mock_completion.call_count == 3

    @patch("kaizen.llm.tips.clustering.completion")
    @patch("kaizen.llm.tips.clustering.supports_response_schema", return_value=False)
    @patch("kaizen.llm.tips.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_raises_after_max_retries(self, _mock_params, _mock_schema, mock_completion):
        mock_completion.side_effect = ValueError("always fails")

        entities = [_make_entity("1", "Tip A"), _make_entity("2", "Tip B")]

        with pytest.raises(KaizenException, match="Failed to combine cluster tips after 3 attempts"):
            combine_cluster(entities)

        assert mock_completion.call_count == 3

    @patch("kaizen.llm.tips.clustering.completion")
    @patch("kaizen.llm.tips.clustering.supports_response_schema", return_value=True)
    @patch("kaizen.llm.tips.clustering.get_supported_openai_params", return_value=["response_format"])
    def test_combine_cluster_uses_structured_output(self, _mock_params, _mock_schema, mock_completion):
        mock_completion.return_value = _mock_completion_response(SAMPLE_TIPS[:1])

        entities = [_make_entity("1", "Tip A"), _make_entity("2", "Tip B")]
        result = combine_cluster(entities)

        assert len(result) == 1
        # Verify response_format was passed
        _, kwargs = mock_completion.call_args
        assert "response_format" in kwargs


# ---------------------------------------------------------------------------
# consolidate_tips tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConsolidateTips:
    @patch("kaizen.llm.tips.clustering.combine_cluster")
    def test_consolidate_tips_deletes_originals_and_inserts_new(self, mock_combine):
        consolidated = [
            Tip(content="Combined tip", rationale="Merged", category="strategy", trigger="Always"),
        ]
        mock_combine.return_value = consolidated

        entities_cluster = [
            _make_entity("1", "Tip A", "error handling"),
            _make_entity("2", "Tip B", "error handling"),
        ]

        mock_backend = MagicMock()
        mock_backend.search_entities.return_value = entities_cluster

        from kaizen.frontend.client.kaizen_client import KaizenClient

        client = KaizenClient.__new__(KaizenClient)
        client.backend = mock_backend
        client.config = MagicMock()
        client.config.clustering_threshold = 0.80

        # Mock cluster_tips to return our cluster
        with patch.object(client, "cluster_tips", return_value=[entities_cluster]):
            client.consolidate_tips("test-ns")

        # Verify insert was called with correct args
        assert mock_backend.update_entities.call_count == 1
        call_args = mock_backend.update_entities.call_args
        ns_id, new_entities, enable_cr = call_args[0]
        assert ns_id == "test-ns"
        assert len(new_entities) == 1
        assert new_entities[0].content == "Combined tip"
        assert new_entities[0].metadata["task_description"] == "error handling"
        assert enable_cr is False

        # Verify deletes were called for each original entity
        assert mock_backend.delete_entity_by_id.call_count == 2
        mock_backend.delete_entity_by_id.assert_any_call("test-ns", "1")
        mock_backend.delete_entity_by_id.assert_any_call("test-ns", "2")

        # Verify insert happened before deletes
        call_names = [str(c) for c in mock_backend.mock_calls]
        insert_idx = next(i for i, c in enumerate(call_names) if "update_entities" in c)
        first_delete_idx = next(i for i, c in enumerate(call_names) if "delete_entity_by_id" in c)
        assert insert_idx < first_delete_idx

    @patch("kaizen.llm.tips.clustering.combine_cluster")
    def test_consolidate_tips_returns_correct_counts(self, mock_combine):
        # Cluster 1: 3 entities -> 1 consolidated tip
        # Cluster 2: 2 entities -> 2 consolidated tips
        mock_combine.side_effect = [
            [Tip(content="C1", rationale="R", category="strategy", trigger="T")],
            [
                Tip(content="C2a", rationale="R", category="strategy", trigger="T"),
                Tip(content="C2b", rationale="R", category="optimization", trigger="T"),
            ],
        ]

        cluster1 = [_make_entity(f"c1-{i}", f"Tip {i}", "task A") for i in range(3)]
        cluster2 = [_make_entity(f"c2-{i}", f"Tip {i}", "task B") for i in range(2)]

        mock_backend = MagicMock()

        from kaizen.frontend.client.kaizen_client import KaizenClient

        client = KaizenClient.__new__(KaizenClient)
        client.backend = mock_backend
        client.config = MagicMock()
        client.config.clustering_threshold = 0.80

        with patch.object(client, "cluster_tips", return_value=[cluster1, cluster2]):
            result = client.consolidate_tips("test-ns")

        assert isinstance(result, ConsolidationResult)
        assert result.clusters_found == 2
        assert result.tips_before == 5
        assert result.tips_after == 3
