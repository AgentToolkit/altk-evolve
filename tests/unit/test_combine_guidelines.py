"""Unit tests for guideline combining and consolidation logic."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from altk_evolve.llm.guidelines import clustering as clustering_module
from altk_evolve.llm.guidelines.clustering import combine_cluster
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.schema.guidelines import ConsolidationResult, Guideline


def _make_entity(
    entity_id: str,
    content: str,
    task_description: str = "do a task",
    support: int = 1,
    evidence: str | None = None,
) -> RecordedEntity:
    metadata: dict = {
        "task_description": task_description,
        "rationale": "some rationale",
        "category": "strategy",
        "trigger": "when needed",
        "support": support,
    }
    if evidence is not None:
        metadata["evidence"] = evidence
    return RecordedEntity(
        id=entity_id,
        content=content,
        type="guideline",
        metadata=metadata,
        created_at=datetime(2025, 1, 1),
    )


def _mock_completion_response(guidelines: list[dict]) -> MagicMock:
    """Build a mock litellm completion response (guidelines must carry source_indices)."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps({"guidelines": guidelines})
    return response


def _cg(content: str, category: str, source_indices: list[int]) -> dict:
    return {
        "content": content,
        "rationale": "why",
        "category": category,
        "trigger": "when",
        "source_indices": source_indices,
    }


SAMPLE_GUIDELINES = [
    _cg("Use retry logic for flaky APIs", "recovery", [0]),
    _cg("Log errors with context", "optimization", [1]),
]


# ---------------------------------------------------------------------------
# combine_cluster tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCombineCluster:
    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_returns_guidelines(self, _mock_params, _mock_schema, mock_completion):
        mock_completion.return_value = _mock_completion_response(SAMPLE_GUIDELINES)

        entities = [
            _make_entity("1", "Always retry on failure"),
            _make_entity("2", "Add error logging"),
        ]

        result = combine_cluster(entities)

        assert len(result) == 2
        assert all(isinstance(t, Guideline) for t in result)
        assert result[0].content == "Use retry logic for flaky APIs"
        assert result[1].category == "optimization"
        mock_completion.assert_called_once()

    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_sums_support_and_merges_evidence(self, _mock_params, _mock_schema, mock_completion):
        # One consolidated guideline subsumes both inputs -> support 2+3=5, evidence success+failure=both.
        mock_completion.return_value = _mock_completion_response([_cg("Merged rule", "strategy", [0, 1])])

        entities = [
            _make_entity("1", "Rule A", support=2, evidence="success"),
            _make_entity("2", "Rule B", support=3, evidence="failure"),
        ]

        result = combine_cluster(entities)

        assert len(result) == 1
        assert result[0].support == 5
        assert result[0].evidence == "both"

    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_carries_uncovered_members(self, _mock_params, _mock_schema, mock_completion):
        # Model only covers index 0; index 1 must survive as its own guideline (lossless fail-safe).
        mock_completion.return_value = _mock_completion_response([_cg("Merged rule", "strategy", [0])])

        entities = [
            _make_entity("1", "Rule A", support=2, evidence="success"),
            _make_entity("2", "Rule B is unique", support=1, evidence="failure"),
        ]

        result = combine_cluster(entities)

        assert len(result) == 2
        # Total support is conserved regardless of what the model covered.
        assert sum(g.support for g in result) == 3
        carried = next(g for g in result if g.content == "Rule B is unique")
        assert carried.support == 1
        assert carried.evidence == "failure"

    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_dedupes_repeated_source_indices(self, _mock_params, _mock_schema, mock_completion):
        # A repeated index within one guideline's source_indices must not double-count support.
        mock_completion.return_value = _mock_completion_response([_cg("Merged rule", "strategy", [0, 0, 1])])

        entities = [
            _make_entity("1", "Rule A", support=2),
            _make_entity("2", "Rule B", support=3),
        ]

        result = combine_cluster(entities)

        assert len(result) == 1
        # 2 + 3 == 5, NOT 2 + 2 + 3.
        assert result[0].support == 5
        assert sum(g.support for g in result) == 5

    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_lossy_uses_aggressive_prompt(self, _mock_params, _mock_schema, mock_completion):
        mock_completion.return_value = _mock_completion_response([_cg("Merged", "strategy", [0, 1])])

        entities = [_make_entity("1", "A"), _make_entity("2", "B")]
        combine_cluster(entities, mode="lossy")

        _, kwargs = mock_completion.call_args
        prompt = kwargs["messages"][0]["content"]
        assert "Merge liberally" in prompt

    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_retries_on_failure(self, _mock_params, _mock_schema, mock_completion):
        mock_completion.side_effect = [
            ValueError("bad json"),
            ValueError("bad json again"),
            _mock_completion_response([_cg("Use retry logic for flaky APIs", "recovery", [0, 1])]),
        ]

        entities = [_make_entity("1", "Guideline A"), _make_entity("2", "Guideline B")]
        result = combine_cluster(entities)

        assert len(result) == 1
        assert result[0].content == "Use retry logic for flaky APIs"
        assert mock_completion.call_count == 3

    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_raises_after_max_retries(self, _mock_params, _mock_schema, mock_completion):
        mock_completion.side_effect = ValueError("always fails")

        entities = [_make_entity("1", "Guideline A"), _make_entity("2", "Guideline B")]

        with pytest.raises(EvolveException, match="Failed to combine cluster guidelines after 3 attempts"):
            combine_cluster(entities)

        assert mock_completion.call_count == 3

    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=True)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=["response_format"])
    def test_combine_cluster_uses_structured_output(self, _mock_params, _mock_schema, mock_completion, monkeypatch):
        monkeypatch.setattr(clustering_module.llm_settings, "guidelines_model", "gpt-4o")
        monkeypatch.setattr(clustering_module.llm_settings, "custom_llm_provider", "openai")
        mock_completion.return_value = _mock_completion_response([_cg("Merged", "strategy", [0, 1])])

        entities = [_make_entity("1", "Guideline A"), _make_entity("2", "Guideline B")]
        result = combine_cluster(entities)

        assert len(result) == 1
        # Verify response_format was passed
        _, kwargs = mock_completion.call_args
        assert "response_format" in kwargs

    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=True)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=["response_format"])
    def test_combine_cluster_uses_json_prompt_for_groq_even_when_schema_is_reported(
        self,
        _mock_params,
        _mock_schema,
        mock_completion,
        monkeypatch,
    ):
        monkeypatch.setattr(clustering_module.llm_settings, "guidelines_model", "groq/openai/gpt-oss-120b")
        monkeypatch.setattr(clustering_module.llm_settings, "custom_llm_provider", "groq")
        mock_completion.return_value = _mock_completion_response([_cg("Merged", "strategy", [0, 1])])

        entities = [_make_entity("1", "Guideline A"), _make_entity("2", "Guideline B")]
        result = combine_cluster(entities)

        assert len(result) == 1
        _, kwargs = mock_completion.call_args
        assert "response_format" not in kwargs
        assert kwargs["custom_llm_provider"] == "groq"
        assert "Output Format (JSON)" in kwargs["messages"][0]["content"]


# ---------------------------------------------------------------------------
# consolidate_guidelines tests
# ---------------------------------------------------------------------------


def _make_client(mock_backend, mode: str = "lossless"):
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    client = EvolveClient.__new__(EvolveClient)
    client.backend = mock_backend
    client.config = MagicMock()
    client.config.clustering_threshold = 0.80
    client.config.consolidation_mode = mode
    return client


@pytest.mark.unit
class TestConsolidateGuidelines:
    @patch("altk_evolve.llm.guidelines.clustering.combine_cluster")
    def test_consolidate_guidelines_deletes_originals_and_inserts_new(self, mock_combine):
        consolidated = [
            Guideline(content="Combined guideline", rationale="Merged", category="strategy", trigger="Always", support=2),
        ]
        mock_combine.return_value = consolidated

        entities_cluster = [
            _make_entity("1", "Guideline A", "error handling"),
            _make_entity("2", "Guideline B", "error handling"),
        ]

        mock_backend = MagicMock()
        # Consolidation reads via the INTERNAL seam (_search_entities_impl), not
        # public search_entities, so a redacting post_read plugin can't make it
        # persist the redacted view back to the store.
        mock_backend._search_entities_impl.return_value = entities_cluster
        client = _make_client(mock_backend)

        with patch.object(client, "_cluster_guideline_entities", return_value=[entities_cluster]):
            client.consolidate_guidelines("test-ns")

        # The read must NOT go through the public (post_read-firing) API.
        mock_backend.search_entities.assert_not_called()
        mock_backend._search_entities_impl.assert_called_once()

        # Verify insert was called with correct args
        assert mock_backend.update_entities.call_count == 1
        call_args = mock_backend.update_entities.call_args
        ns_id, new_entities, enable_cr = call_args[0]
        assert ns_id == "test-ns"
        assert len(new_entities) == 1
        assert new_entities[0].content == "Combined guideline"
        assert new_entities[0].metadata["task_description"] == "error handling"
        assert new_entities[0].metadata["support"] == 2
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

    @patch("altk_evolve.llm.guidelines.clustering.combine_cluster")
    def test_consolidate_guidelines_returns_correct_counts_and_conserves_support(self, mock_combine):
        # Cluster 1: 3 entities -> 1 consolidated guideline (support 3)
        # Cluster 2: 2 entities -> 2 consolidated guidelines (support 1 + 1)
        mock_combine.side_effect = [
            [Guideline(content="C1", rationale="R", category="strategy", trigger="T", support=3)],
            [
                Guideline(content="C2a", rationale="R", category="strategy", trigger="T", support=1),
                Guideline(content="C2b", rationale="R", category="optimization", trigger="T", support=1),
            ],
        ]

        cluster1 = [_make_entity(f"c1-{i}", f"Guideline {i}", "task A") for i in range(3)]
        cluster2 = [_make_entity(f"c2-{i}", f"Guideline {i}", "task B") for i in range(2)]

        mock_backend = MagicMock()
        mock_backend._search_entities_impl.return_value = cluster1 + cluster2
        client = _make_client(mock_backend)

        with patch.object(client, "_cluster_guideline_entities", return_value=[cluster1, cluster2]):
            result = client.consolidate_guidelines("test-ns")

        assert isinstance(result, ConsolidationResult)
        assert result.clusters_found == 2
        assert result.guidelines_before == 5
        assert result.guidelines_after == 3
        # 5 originals (support 1 each) -> support conserved at 5 (3 + 1 + 1).
        assert result.support_before == 5
        assert result.support_after == 5

    def test_consolidate_guidelines_none_mode_is_noop(self):
        mock_backend = MagicMock()
        client = _make_client(mock_backend, mode="none")

        result = client.consolidate_guidelines("test-ns")

        assert result == ConsolidationResult(clusters_found=0, guidelines_before=0, guidelines_after=0)
        mock_backend.update_entities.assert_not_called()
        mock_backend.delete_entity_by_id.assert_not_called()
