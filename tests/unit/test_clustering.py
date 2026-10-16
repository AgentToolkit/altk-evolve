"""Unit tests for guideline clustering logic."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from altk_evolve.llm.guidelines.clustering import _attribute_support, _split_oversized, _union_find, cluster_entities
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.schema.guidelines import ConsolidatedGuideline


def _make_entity(entity_id: str, task_description: str | None = None, content: str | None = None) -> RecordedEntity:
    """Helper to create a RecordedEntity with optional task_description metadata."""
    metadata = {}
    if task_description is not None:
        metadata["task_description"] = task_description
    return RecordedEntity(
        id=entity_id,
        content=content if content is not None else f"Guideline for {entity_id}",
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
@patch("altk_evolve.llm.guidelines.clustering._get_sentence_transformer")
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

    @patch("altk_evolve.config.milvus.milvus_other_settings")
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

    def test_guideline_text_key_clusters_the_same_advice_across_tasks(self, mock_st_cls):
        mock_model = MagicMock()
        mock_model.encode = _mock_encode
        mock_st_cls.return_value = mock_model

        entities = [
            _make_entity("1", "Set up logging", content="Retry on error handling failures"),
            _make_entity("2", "Add a caching layer", content="Wrap calls in error handling"),
            # No task description: left out by the task key, clustered by the text key.
            _make_entity("3", None, content="Add error handling around I/O"),
            _make_entity("4", "Ship logging to the collector", content="Enable caching for repeated lookups"),
        ]

        by_text = cluster_entities(entities, threshold=0.9, embedding_model="test-model", key="guideline_text")
        by_task = cluster_entities(entities, threshold=0.9, embedding_model="test-model")

        assert [[e.id for e in c] for c in by_text] == [["1", "2", "3"]]
        # The task key pairs the two logging guidelines, which give unrelated advice.
        assert [[e.id for e in c] for c in by_task] == [["1", "4"]]

    def test_guideline_text_key_leaves_out_whitespace_only_content(self, mock_st_cls):
        mock_model = MagicMock()
        mock_model.encode = MagicMock(side_effect=_mock_encode)
        mock_st_cls.return_value = mock_model

        entities = [
            _make_entity("1", content="Add error handling"),
            _make_entity("2", content="  \n\t"),
            _make_entity("3", content="More error handling"),
        ]

        clusters = cluster_entities(entities, threshold=0.9, embedding_model="test-model", key="guideline_text")

        assert [[e.id for e in c] for c in clusters] == [["1", "3"]]
        assert mock_model.encode.call_args.args[0] == ["Add error handling", "More error handling"]

    def test_max_cluster_size_splits_a_large_cluster(self, mock_st_cls):
        mock_model = MagicMock()
        mock_model.encode = _mock_encode
        mock_st_cls.return_value = mock_model

        entities = [_make_entity(str(i), f"error handling {i}") for i in range(5)]

        clusters = cluster_entities(entities, threshold=0.9, embedding_model="test-model", max_cluster_size=2)

        # 5 -> [2, 2, 1]; the leftover singleton forms no cluster.
        assert [[e.id for e in c] for c in clusters] == [["0", "1"], ["2", "3"]]

    def test_rejects_an_unknown_key_and_a_cap_below_two(self, mock_st_cls):
        entities = [_make_entity("1", "a"), _make_entity("2", "b")]
        with pytest.raises(ValueError, match="clustering key"):
            cluster_entities(entities, embedding_model="test-model", key="content")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="at least 2"):
            cluster_entities(entities, embedding_model="test-model", max_cluster_size=1)
        mock_st_cls.assert_not_called()


# ---------------------------------------------------------------------------
# _split_oversized tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSplitOversized:
    def test_a_group_within_the_limit_is_unchanged(self):
        assert _split_oversized([0, 1, 2], np.eye(3), 3, 0.5) == [[0, 1, 2]]

    def test_each_chunk_takes_the_seeds_nearest_neighbours(self):
        # A chain 0~1~2~3: 0 is closest to 2, and 1 is closest to 3.
        similarity = np.array(
            [
                [1.0, 0.1, 0.9, 0.2],
                [0.1, 1.0, 0.3, 0.8],
                [0.9, 0.3, 1.0, 0.4],
                [0.2, 0.8, 0.4, 1.0],
            ]
        )

        # At threshold 0.0 every neighbour qualifies, so only nearest-first ordering picks 2 for 0.
        assert _split_oversized([0, 1, 2, 3], similarity, 2, 0.0) == [[0, 2], [1, 3]]

    def test_chunks_only_join_entities_similar_enough_to_cluster(self):
        # A star: five leaves at 0.75 to the hub (index 5) and 0.5 to each other.
        similarity = np.full((6, 6), 0.5)
        similarity[5, :] = similarity[:, 5] = 0.75
        np.fill_diagonal(similarity, 1.0)

        chunks = _split_oversized(list(range(6)), similarity, 2, 0.7)

        # Leaf 0 takes the hub; the other leaves are not similar to each other, so none pair up.
        assert chunks == [[0, 5], [1], [2], [3], [4]]

    def test_chunks_cover_the_group_and_respect_the_limit(self):
        rng = np.random.default_rng(0)
        vectors = rng.normal(size=(11, 4))
        similarity = vectors @ vectors.T

        chunks = _split_oversized(list(range(11)), similarity, 3, float("-inf"))

        assert sorted(i for chunk in chunks for i in chunk) == list(range(11))
        assert all(len(chunk) <= 3 for chunk in chunks)


# ---------------------------------------------------------------------------
# _attribute_support tests
# ---------------------------------------------------------------------------


def _consolidated(content: str, source_indices: list) -> ConsolidatedGuideline:
    return ConsolidatedGuideline(content=content, rationale="", category="strategy", trigger="", source_indices=source_indices)


@pytest.mark.unit
class TestAttributeSupport:
    def test_members_partition_across_outputs(self):
        entities = [_make_entity(str(i), "task") for i in range(4)]
        consolidated = [_consolidated("first", [0, 1]), _consolidated("second", [1, 2])]

        out = _attribute_support(entities, consolidated, [1, 2, 3, 4], [None] * 4)

        assert [(g.content, members) for g, members in out] == [
            ("first", [0, 1]),
            # Index 1 was already claimed, so only index 2 counts here.
            ("second", [2]),
            # Index 3 was never claimed and is carried through as its own guideline.
            ("Guideline for 3", [3]),
        ]
        assert sorted(i for _, members in out for i in members) == [0, 1, 2, 3]
        assert [g.support for g, _ in out] == [3, 3, 4]

    def test_output_without_attributable_members_is_dropped(self):
        entities = [_make_entity(str(i), "task") for i in range(2)]
        consolidated = [_consolidated("empty", []), _consolidated("out of range", [5, -1]), _consolidated("real", [0, 1])]

        out = _attribute_support(entities, consolidated, [1, 1], [None, None])

        assert [(g.content, members) for g, members in out] == [("real", [0, 1])]
