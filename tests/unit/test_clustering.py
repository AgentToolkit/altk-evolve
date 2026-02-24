"""Unit tests for tip clustering logic."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from kaizen.llm.tips.clustering import _union_find, cluster_entities
from kaizen.schema.core import RecordedEntity


def _make_entity(entity_id: str, task_description: str | None = None) -> RecordedEntity:
    """Helper to create a RecordedEntity with optional task_description metadata."""
    metadata = {}
    if task_description is not None:
        metadata["task_description"] = task_description
    return RecordedEntity(
        id=entity_id,
        content=f"Tip for {entity_id}",
        type="guideline",
        metadata=metadata,
        created_at=datetime(2025, 1, 1),
    )


# ---------------------------------------------------------------------------
# _union_find tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnionFind:
    def test_no_pairs(self):
        groups = _union_find(3, [])
        assert len(groups) == 3
        for g in groups:
            assert len(g) == 1

    def test_single_pair(self):
        groups = _union_find(3, [(0, 1)])
        sizes = sorted(len(g) for g in groups)
        assert sizes == [1, 2]

    def test_transitive_merge(self):
        groups = _union_find(4, [(0, 1), (1, 2)])
        sizes = sorted(len(g) for g in groups)
        assert sizes == [1, 3]

    def test_all_connected(self):
        groups = _union_find(3, [(0, 1), (1, 2)])
        sizes = sorted(len(g) for g in groups)
        assert sizes == [3]

    def test_two_components(self):
        groups = _union_find(4, [(0, 1), (2, 3)])
        sizes = sorted(len(g) for g in groups)
        assert sizes == [2, 2]


# ---------------------------------------------------------------------------
# cluster_entities tests
# ---------------------------------------------------------------------------


def _mock_encode(descriptions, normalize_embeddings=True):
    """Return controlled embeddings: identical vectors for similar, orthogonal for different."""
    vectors = []
    for desc in descriptions:
        if "error handling" in desc.lower():
            vectors.append([1.0, 0.0, 0.0])
        elif "caching" in desc.lower():
            vectors.append([0.0, 1.0, 0.0])
        elif "logging" in desc.lower():
            vectors.append([0.0, 0.0, 1.0])
        else:
            vectors.append([0.5, 0.5, 0.0])
    return np.array(vectors)


@pytest.mark.unit
@patch("kaizen.llm.tips.clustering._get_sentence_transformer")
class TestClusterEntities:
    def test_groups_similar_tasks(self, mock_st_cls):
        mock_model = MagicMock()
        mock_model.encode = _mock_encode
        mock_st_cls.return_value = mock_model

        entities = [
            _make_entity("1", "Improve error handling in API"),
            _make_entity("2", "Better error handling for edge cases"),
            _make_entity("3", "Add caching to database queries"),
        ]

        clusters = cluster_entities(entities, threshold=0.9, embedding_model="test-model")

        # The two error handling entities should cluster together
        assert len(clusters) == 1
        cluster_ids = {e.id for e in clusters[0]}
        assert cluster_ids == {"1", "2"}

    def test_separates_different_tasks(self, mock_st_cls):
        mock_model = MagicMock()
        mock_model.encode = _mock_encode
        mock_st_cls.return_value = mock_model

        entities = [
            _make_entity("1", "Improve error handling in API"),
            _make_entity("2", "Add caching to database queries"),
            _make_entity("3", "Set up logging infrastructure"),
        ]

        clusters = cluster_entities(entities, threshold=0.9, embedding_model="test-model")

        # All orthogonal — no clusters
        assert clusters == []

    def test_skips_missing_task_description(self, mock_st_cls):
        mock_model = MagicMock()
        mock_model.encode = _mock_encode
        mock_st_cls.return_value = mock_model

        entities = [
            _make_entity("1", "Improve error handling in API"),
            _make_entity("2", None),  # no task_description
            _make_entity("3", "Better error handling for edge cases"),
        ]

        clusters = cluster_entities(entities, threshold=0.9, embedding_model="test-model")

        # Entity 2 is excluded; entities 1 and 3 should cluster
        assert len(clusters) == 1
        cluster_ids = {e.id for e in clusters[0]}
        assert cluster_ids == {"1", "3"}

    def test_empty_input(self, mock_st_cls):
        clusters = cluster_entities([], threshold=0.8, embedding_model="test-model")
        assert clusters == []
        mock_st_cls.assert_not_called()

    def test_single_entity(self, mock_st_cls):
        entities = [_make_entity("1", "Some task")]
        clusters = cluster_entities(entities, threshold=0.8, embedding_model="test-model")
        assert clusters == []
        mock_st_cls.assert_not_called()

    @patch("kaizen.config.milvus.milvus_other_settings")
    def test_uses_default_embedding_model(self, mock_settings, mock_st_cls):
        mock_settings.embedding_model = "test-default-model"
        mock_model = MagicMock()
        mock_model.encode = _mock_encode
        mock_st_cls.return_value = mock_model

        entities = [
            _make_entity("1", "Improve error handling"),
            _make_entity("2", "Better error handling"),
        ]

        cluster_entities(entities, threshold=0.9)

        # Should use the default model from config
        mock_st_cls.assert_called_once_with("test-default-model")
