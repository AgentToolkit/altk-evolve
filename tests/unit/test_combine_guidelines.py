"""Unit tests for guideline combining and consolidation logic."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from altk_evolve.backend.filesystem import FilesystemEntityBackend
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.llm.guidelines import clustering as clustering_module
from altk_evolve.llm.guidelines.clustering import combine_cluster
from altk_evolve.schema.core import Entity, RecordedEntity
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
    def test_combine_cluster_repairs_a_multi_valued_category(self, _mock_params, _mock_schema, mock_completion):
        """A merge response labelled "strategy|recovery" is repaired, not retried three times.

        ``ConsolidatedGuideline`` inherits the category repair, so the same gpt-oss output that
        cost a whole extraction used to cost the cluster: validation failed on every attempt and
        ``combine_cluster`` raised, losing the merge for a label it could read.
        """
        mock_completion.return_value = _mock_completion_response([_cg("Merged rule", "strategy|recovery", [0, 1])])

        entities = [_make_entity("1", "Rule A", support=2), _make_entity("2", "Rule B", support=1)]
        result = combine_cluster(entities)

        assert mock_completion.call_count == 1
        assert len(result) == 1
        assert result[0].category == "strategy"
        assert result[0].support == 3

    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_normalizes_stored_categories(self, _mock_params, _mock_schema, mock_completion):
        """Stored labels are resolved on both sides of the LLM call.

        Going in, because a multi-valued label shown back to the model invites it to echo one;
        coming out, because an uncovered member is rebuilt from its metadata and a stored label
        that names nothing has to fall back — a stored entity cannot be regenerated.
        """
        mock_completion.return_value = _mock_completion_response([_cg("Merged rule", "strategy", [0])])

        covered = _make_entity("1", "Rule A")
        covered.metadata["category"] = "optimization|strategy"
        uncovered = _make_entity("2", "Rule B is unique")
        uncovered.metadata["category"] = "escalation"
        result = combine_cluster([covered, uncovered])

        _, kwargs = mock_completion.call_args
        prompt = kwargs["messages"][0]["content"]
        assert "optimization|strategy" not in prompt
        assert "**Category:** optimization" in prompt
        carried = next(g for g in result if g.content == "Rule B is unique")
        assert carried.category == "strategy"

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
    def test_combine_cluster_prompt_names_what_the_cluster_shares(self, _mock_params, _mock_schema, mock_completion):
        mock_completion.return_value = _mock_completion_response([_cg("Merged", "strategy", [0, 1])])
        entities = [_make_entity("1", "A"), _make_entity("2", "B")]

        combine_cluster(entities)
        by_task = mock_completion.call_args.kwargs["messages"][0]["content"]
        combine_cluster(entities, clustering_key="guideline_text")
        by_text = mock_completion.call_args.kwargs["messages"][0]["content"]

        # The default prompt is unchanged; clustering on text says the advice, not the task, is shared.
        assert by_task.splitlines()[0] == "You are consolidating multiple AI agent guidelines that were generated from similar tasks."
        assert by_text.splitlines()[0] == (
            "You are consolidating multiple AI agent guidelines that give similar advice, possibly learned on different tasks."
        )
        assert by_task.splitlines()[1:] == by_text.splitlines()[1:]

    @patch("altk_evolve.llm.guidelines.clustering.completion")
    @patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False)
    @patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[])
    def test_combine_cluster_prompt_omits_the_task_section_without_tasks(self, _mock_params, _mock_schema, mock_completion):
        mock_completion.return_value = _mock_completion_response([_cg("Merged", "strategy", [0, 1])])

        combine_cluster([_make_entity("1", "A", ""), _make_entity("2", "B", "")], clustering_key="guideline_text")
        prompt = mock_completion.call_args.kwargs["messages"][0]["content"]

        assert "# Task Descriptions" not in prompt
        assert "came from tasks like" not in prompt
        assert "## Guideline 0" in prompt

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
    client.config.clustering_key = "task_description"
    client.config.consolidation_max_cluster_size = None
    client.config.consolidation_max_rounds = 1
    return client


@pytest.mark.unit
class TestConsolidateGuidelines:
    @patch("altk_evolve.llm.guidelines.clustering.combine_cluster_with_members")
    def test_consolidate_guidelines_deletes_originals_and_inserts_new(self, mock_combine):
        entities_cluster = [
            _make_entity("1", "Guideline A", "error handling"),
            _make_entity("2", "Guideline B", "error handling"),
        ]
        mock_combine.return_value = [
            (
                Guideline(content="Combined guideline", rationale="Merged", category="strategy", trigger="Always", support=2),
                entities_cluster,
            ),
        ]

        mock_backend = MagicMock()
        # Consolidation reads via the administrative scan seam, not
        # public search_entities, so a redacting post_read plugin can't make it
        # persist the redacted view back to the store.
        mock_backend.scan_entities.return_value = entities_cluster
        client = _make_client(mock_backend)

        with patch.object(client, "_cluster_guideline_entities", return_value=[entities_cluster]):
            client.consolidate_guidelines("test-ns")

        # The read must NOT go through the public (post_read-firing) API.
        mock_backend.search_entities.assert_not_called()
        mock_backend.scan_entities.assert_called_once_with("test-ns", filters={"type": "guideline"}, limit=10000)

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

    @patch("altk_evolve.llm.guidelines.clustering.combine_cluster_with_members")
    def test_consolidate_guidelines_returns_correct_counts_and_conserves_support(self, mock_combine):
        cluster1 = [_make_entity(f"c1-{i}", f"Guideline {i}", "task A") for i in range(3)]
        cluster2 = [_make_entity(f"c2-{i}", f"Guideline {i}", "task B") for i in range(2)]

        # Cluster 1: 3 entities -> 1 consolidated guideline (support 3)
        # Cluster 2: 2 entities -> 2 consolidated guidelines (support 1 + 1)
        mock_combine.side_effect = [
            [(Guideline(content="C1", rationale="R", category="strategy", trigger="T", support=3), cluster1)],
            [
                (Guideline(content="C2a", rationale="R", category="strategy", trigger="T", support=1), cluster2[:1]),
                (Guideline(content="C2b", rationale="R", category="optimization", trigger="T", support=1), cluster2[1:]),
            ],
        ]

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

        assert result == ConsolidationResult(clusters_found=0, guidelines_before=0, guidelines_after=0, rounds=0)
        mock_backend.update_entities.assert_not_called()
        mock_backend.delete_entity_by_id.assert_not_called()

    @patch("altk_evolve.llm.guidelines.clustering.combine_cluster_with_members")
    def test_consolidate_guidelines_passes_the_clustering_options_through(self, mock_combine):
        first, second, third = (_make_entity("1", "A", "task a"), _make_entity("2", "B", "task b"), _make_entity("3", "C", "task c"))
        mock_combine.return_value = [
            (Guideline(content="A", rationale="R", category="strategy", trigger="T", support=1), [first]),
            (Guideline(content="B and C", rationale="R", category="strategy", trigger="T", support=2), [second, third]),
        ]
        client = _make_client(MagicMock())

        with patch.object(client, "_cluster_guideline_entities", return_value=[[first, second, third]]) as cluster:
            client.consolidate_guidelines("test-ns", key="guideline_text", max_cluster_size=4)

        assert cluster.call_args.kwargs == {"threshold": None, "key": "guideline_text", "max_cluster_size": 4}
        assert mock_combine.call_args.kwargs == {"mode": "lossless", "clustering_key": "guideline_text"}
        # Each output takes its task description from its own members, not from the cluster's
        # first; one whose members span different tasks keeps none.
        new_entities = client.backend.update_entities.call_args[0][1]
        assert [e.metadata["task_description"] for e in new_entities] == ["task a", ""]
        assert [e.metadata.get("source_tasks") for e in new_entities] == [None, ["task b", "task c"]]

    def test_merged_task_metadata_depends_on_the_clustering_key(self):
        from altk_evolve.frontend.client.evolve_client import _merged_task_metadata

        a, b, a_again = _make_entity("1", "x", "task a"), _make_entity("2", "y", "task b"), _make_entity("3", "z", "task a")
        untasked = _make_entity("4", "w", "")
        earlier_merge = _make_entity("5", "v", "")
        earlier_merge.metadata["source_tasks"] = ["task c", "task a"]

        # Clustered by task, the members' tasks are similar: the first is kept.
        assert _merged_task_metadata([a, b], "task_description") == {"task_description": "task a"}
        # Clustered by text, unrelated tasks would tie retrieval by source task to one of them.
        assert _merged_task_metadata([a, b], "guideline_text") == {"task_description": "", "source_tasks": ["task a", "task b"]}
        assert _merged_task_metadata([untasked, a, a_again], "guideline_text") == {"task_description": "task a"}
        assert _merged_task_metadata([untasked], "guideline_text") == {"task_description": ""}
        # An earlier cross-task merge contributes its source tasks, not its empty description.
        assert _merged_task_metadata([earlier_merge, a], "guideline_text") == {
            "task_description": "",
            "source_tasks": ["task c", "task a"],
        }
        # A task description set on it later (an update or a patch) is kept alongside them.
        earlier_merge.metadata["task_description"] = "task d"
        assert _merged_task_metadata([earlier_merge, b], "guideline_text") == {
            "task_description": "",
            "source_tasks": ["task c", "task a", "task d", "task b"],
        }

    @pytest.mark.parametrize("malformed", [5, "abc", {"x": 1}, None])
    def test_merged_task_metadata_ignores_a_source_tasks_value_that_is_not_a_list(self, malformed):
        from altk_evolve.frontend.client.evolve_client import _merged_task_metadata

        # An int would raise inside the merge and skip the cluster on every run; a string or a
        # dict would be stored as its characters or keys.
        first, second = _make_entity("1", "x", "t1"), _make_entity("2", "y", "t2")
        first.metadata["source_tasks"] = malformed

        assert _merged_task_metadata([first, second], "guideline_text") == {"task_description": "", "source_tasks": ["t1", "t2"]}

    def test_consolidate_guidelines_rejects_zero_rounds(self):
        with pytest.raises(ValueError, match="max_rounds"):
            _make_client(MagicMock()).consolidate_guidelines("test-ns", max_rounds=0)


@pytest.mark.unit
def test_config_validates_the_consolidation_knobs():
    fields = EvolveConfig.model_fields
    assert fields["clustering_key"].default == "task_description"
    assert fields["consolidation_max_cluster_size"].default is None
    assert fields["consolidation_max_rounds"].default == 1
    for bad in ({"clustering_key": "content"}, {"consolidation_max_cluster_size": 1}, {"consolidation_max_rounds": 0}):
        with pytest.raises(ValidationError):
            EvolveConfig(_env_file=None, **bad)


# ---------------------------------------------------------------------------
# Multi-round consolidation, against a real filesystem store
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    backend = FilesystemEntityBackend(FilesystemSettings(data_dir=str(tmp_path)))
    backend.create_namespace("n")
    return backend


def _store_guidelines(store, **tasks: str) -> None:
    """Store one guideline per keyword: its content is the name and its task the value."""
    store.update_entities(
        "n",
        [
            Entity(type="guideline", content=content, metadata={"task_description": task, "category": "strategy", "user_id": "u"})
            for content, task in tasks.items()
        ],
        False,
    )


def _fake_cluster(entities, threshold, key="task_description", max_cluster_size=None, **_):
    """Cluster by task description (or by the content's first letter for the text key), then
    cut each cluster into chunks of max_cluster_size."""
    groups: dict[str, list[RecordedEntity]] = {}
    for entity in sorted(entities, key=lambda e: str(e.content)):
        group = str(entity.content)[0] if key == "guideline_text" else entity.metadata["task_description"]
        groups.setdefault(group, []).append(entity)
    size = max_cluster_size or len(entities)
    chunks = [group[i : i + size] for group in groups.values() for i in range(0, len(group), size)]
    return [chunk for chunk in chunks if len(chunk) > 1]


def _fake_combine(merge_tasks: set[str]):
    """Merge a cluster into one guideline if it was clustered by text or its task is in
    merge_tasks; otherwise keep every member."""

    def combine(cluster, mode="lossless", clustering_key="task_description"):
        def out(content, members):
            support = sum(int(m.metadata.get("support", 1)) for m in members)
            return Guideline(content=content, rationale="", category="strategy", trigger="", support=support), members

        if clustering_key != "guideline_text" and cluster[0].metadata["task_description"] not in merge_tasks:
            return [out(str(m.content), [m]) for m in cluster]
        return [out("+".join(sorted(str(m.content) for m in cluster)), list(cluster))]

    return combine


def _consolidate(store, merge_tasks, undeletable=(), attempts=None, **kwargs):
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    client = EvolveClient.__new__(EvolveClient)
    client.backend = store
    client.config = SimpleNamespace(clustering_threshold=0.8, consolidation_mode="lossless")

    def delete(namespace_id, entity_id):
        # Stands in for a vetoed (legal hold) or failed delete of the named guidelines.
        stored = {str(e.id): str(e.content) for e in store.scan_entities(namespace_id, filters={"type": "guideline"})}
        if attempts is not None:
            attempts.append(stored[str(entity_id)])
        if stored[str(entity_id)] in undeletable:
            raise RuntimeError("delete blocked")
        store.delete_entity_by_id(namespace_id, entity_id)

    with (
        patch.object(clustering_module, "cluster_entities", side_effect=_fake_cluster),
        patch.object(clustering_module, "combine_cluster_with_members", side_effect=_fake_combine(merge_tasks)) as combine,
        patch.object(client, "delete_entity_by_id", side_effect=delete),
    ):
        result = client.consolidate_guidelines("n", **kwargs)
    stored = {str(e.content): e.metadata.get("support") for e in store.scan_entities("n", filters={"type": "guideline"})}
    return result, stored, combine.call_count


@pytest.mark.unit
class TestConsolidationRounds:
    def test_later_rounds_merge_what_the_size_cap_split(self, store):
        _store_guidelines(store, A="x", B="x", C="x", D="x")

        result, stored, _ = _consolidate(store, {"x"}, max_cluster_size=2, max_rounds=3)

        assert stored == {"A+B+C+D": 4}
        # Round 1 merges two chunks, round 2 merges their outputs, round 3 finds nothing to cluster.
        # The before count covers the four stored guidelines once, not round 1's outputs again.
        assert result == ConsolidationResult(
            clusters_found=3, guidelines_before=4, guidelines_after=1, support_before=4, support_after=4, rounds=3
        )

    def test_one_round_by_default(self, store):
        _store_guidelines(store, A="x", B="x", C="x", D="x")

        result, stored, _ = _consolidate(store, {"x"}, max_cluster_size=2)

        assert stored == {"A+B": 2, "C+D": 2}
        assert result == ConsolidationResult(
            clusters_found=2, guidelines_before=4, guidelines_after=2, support_before=4, support_after=4, rounds=1
        )

    def test_stops_after_a_round_that_reduces_nothing_without_re_asking(self, store):
        _store_guidelines(store, A="x", B="x", C="y", D="y")

        result, stored, merge_calls = _consolidate(store, {"y"}, max_rounds=5)

        assert stored == {"A": 1, "B": 1, "C+D": 2}
        # Round 2 clusters A and B again, but they are the outputs of one earlier merge, so
        # the model is not asked again, and with nothing else to merge the run stops.
        assert merge_calls == 2
        assert (result.rounds, result.guidelines_before, result.guidelines_after) == (2, 4, 3)

    def test_an_original_that_could_not_be_deleted_is_not_merged_again(self, store):
        _store_guidelines(store, A="x", B="x")

        result, stored, merge_calls = _consolidate(store, {"x"}, undeletable={"A"}, max_rounds=4)

        # A survives next to the merge that already counts its support. Re-merging it in
        # later rounds would add its support again each round (A+B would reach 5).
        assert stored == {"A": None, "A+B": 2}
        assert merge_calls == 1
        assert (result.rounds, result.guidelines_before, result.support_before, result.support_after) == (2, 2, 2, 2)

    def test_a_later_merge_keeps_the_source_tasks_of_an_earlier_cross_task_merge(self, store):
        _store_guidelines(store, A1="pay a friend", A2="make a playlist", A3="make a playlist")

        result, stored, _ = _consolidate(store, set(), key="guideline_text", max_cluster_size=2, max_rounds=3)

        # Round 1 merges A1 and A2 across tasks; round 2 merges that output with A3. Reading
        # only A3's task in round 2 would tie the result to the playlist task again.
        assert stored == {"A1+A2+A3": 3}
        (merged,) = store.scan_entities("n", filters={"type": "guideline"})
        assert merged.metadata["task_description"] == ""
        assert merged.metadata["source_tasks"] == ["pay a friend", "make a playlist"]
        assert result.rounds == 3


def _stored(store) -> dict[str, RecordedEntity]:
    return {str(e.content): e for e in store.scan_entities("n", filters={"type": "guideline"})}


@pytest.mark.unit
class TestAbsorbedOriginalsAcrossRuns:
    """An original that survives its merge (vetoed or failed delete) is not merged again by a later run (#427)."""

    def test_separate_runs_do_not_re_merge_an_undeleted_original(self, store):
        _store_guidelines(store, A="x", B="x")

        results = [_consolidate(store, {"x"}, undeletable={"A"}) for _ in range(3)]

        # Each run used to cluster A with A+B again, so support went 2 -> 3 -> 4.
        assert [stored for _, stored, _ in results] == [{"A": None, "A+B": 2}] * 3
        assert [calls for _, _, calls in results] == [1, 0, 0]
        assert results[0][0] == ConsolidationResult(
            clusters_found=1, guidelines_before=2, guidelines_after=1, support_before=2, support_after=2, rounds=1
        )
        assert results[2][0] == ConsolidationResult(
            clusters_found=0, guidelines_before=0, guidelines_after=0, support_before=0, support_after=0, rounds=1
        )

    def test_guideline_text_key(self, store):
        _store_guidelines(store, A1="x", A2="y")

        for _ in range(3):
            _, stored, _ = _consolidate(store, set(), undeletable={"A1"}, key="guideline_text")

        assert stored == {"A1": None, "A1+A2": 2}

    def test_a_later_merge_of_the_output_carries_the_absorbed_ids_forward(self, store):
        _store_guidelines(store, A="x", B="x")
        _consolidate(store, {"x"}, undeletable={"A"})
        _store_guidelines(store, C="x")

        _, stored, _ = _consolidate(store, {"x"}, undeletable={"A"})
        assert stored == {"A": None, "A+B+C": 3}
        _, stored, calls = _consolidate(store, {"x"}, undeletable={"A"})

        assert (stored, calls) == ({"A": None, "A+B+C": 3}, 0)
        held = _stored(store)["A"]
        assert held.id in {entry["id"] for entry in _stored(store)["A+B+C"].metadata["merged_from"]}

    def test_a_lifted_hold_lets_a_later_run_finish_the_delete(self, store):
        _store_guidelines(store, A="x", B="x")
        _consolidate(store, {"x"}, undeletable={"A"})

        result, stored, calls = _consolidate(store, {"x"})

        # A's support is already in A+B, and the run that merged it already reported it
        # as removed, so finishing the delete changes neither count.
        assert (stored, calls) == ({"A+B": 2}, 0)
        assert (result.clusters_found, result.guidelines_before, result.guidelines_after) == (0, 0, 0)

    def test_an_original_edited_after_the_merge_is_neither_deleted_nor_merged_again(self, store):
        _store_guidelines(store, A="x", B="x")
        _consolidate(store, {"x"}, undeletable={"A"})
        held = _stored(store)["A"]
        store._patch_entity("n", held.id, "guideline", "A edited", int(held.created_at.timestamp()), held.metadata)

        _, stored, calls = _consolidate(store, {"x"})

        # The edit is not what A+B absorbed, so deleting A would lose it; but A's support is
        # still counted in A+B, so merging it again would count it twice.
        assert (stored, calls) == ({"A edited": None, "A+B": 2}, 0)

    def test_deleting_the_merge_output_makes_the_original_eligible_again(self, store):
        _store_guidelines(store, A="x", B="x")
        _consolidate(store, {"x"}, undeletable={"A"})
        store.delete_entity_by_id("n", _stored(store)["A+B"].id)
        _store_guidelines(store, C="x")

        _, stored, _ = _consolidate(store, {"x"}, undeletable={"A"})

        assert stored == {"A": None, "A+C": 2}

    @pytest.mark.parametrize("published", ["A+B", "A"])
    def test_a_record_still_applies_after_a_scope_change(self, store, published):
        _store_guidelines(store, A="x", B="x")
        _consolidate(store, {"x"}, undeletable={"A"})
        # What publish_entity does: the guideline moves to the public scope.
        public = {"visibility": "public", "owner_id": "u"}
        store.update_entity_metadata("n", _stored(store)[published].id, public)
        # C shares A's scope, so A and C could merge, and A+B is in the other scope.
        c_scope = public if published == "A" else {}
        store.update_entities(
            "n", [Entity(type="guideline", content="C", metadata={"task_description": "x", "user_id": "u", **c_scope})], False
        )

        _, stored, _ = _consolidate(store, {"x"}, undeletable={"A"})

        # A's support is in A+B; merging it with C would count it in A+C too.
        assert stored == {"A": None, "A+B": 2, "C": None}

    @pytest.mark.parametrize(
        "entry",
        [
            {"id": "ID"},
            {"id": "ID", "content_sha256": "[REDACTED]"},
            {"id": "ID", "content_sha256": "0" * 64, "note": "extra"},
            "ID",
        ],
    )
    def test_a_malformed_record_is_ignored(self, store, entry):
        _store_guidelines(store, A="x", B="x")
        target = _stored(store)["A"]
        entry = entry.replace("ID", target.id) if isinstance(entry, str) else {**entry, "id": target.id}
        store.update_entities(
            "n", [Entity(type="guideline", content="other", metadata={"task_description": "z", "merged_from": [entry]})], False
        )

        _, stored, _ = _consolidate(store, {"x"})

        assert stored == {"A+B": 2, "other": None}

    def test_each_run_retries_a_held_delete_once(self, store):
        _store_guidelines(store, A="x", B="x")
        _consolidate(store, {"x"}, undeletable={"A"})
        _store_guidelines(store, C="x")

        attempts: list[str] = []
        result, stored, _ = _consolidate(store, {"x"}, undeletable={"A"}, attempts=attempts, max_rounds=3)

        # The second round finds A absorbed again, but does not retry its delete.
        assert (stored, result.rounds) == ({"A": None, "A+B+C": 3}, 2)
        assert attempts.count("A") == 1

    def test_the_dry_run_listing_leaves_absorbed_originals_out(self, store):
        from altk_evolve.frontend.client.evolve_client import EvolveClient

        _store_guidelines(store, A="x", B="x")
        _consolidate(store, {"x"}, undeletable={"A"})
        _store_guidelines(store, C="x")
        client = EvolveClient.__new__(EvolveClient)
        client.backend = store
        client.config = SimpleNamespace(clustering_threshold=0.8)

        with patch.object(clustering_module, "cluster_entities", side_effect=_fake_cluster):
            clusters = client.cluster_guidelines("n")

        assert [sorted(str(e.content) for e in cluster) for cluster in clusters] == [["A+B", "C"]]

    def test_a_successful_merge_records_its_members(self, store):
        _store_guidelines(store, A="x", B="x")
        ids = {content: e.id for content, e in _stored(store).items()}

        _, stored, _ = _consolidate(store, {"x"})

        assert stored == {"A+B": 2}
        merged_from = _stored(store)["A+B"].metadata["merged_from"]
        assert {entry["id"] for entry in merged_from} == {ids["A"], ids["B"]}
        assert all(len(entry["content_sha256"]) == 64 for entry in merged_from)

    def test_an_entry_is_dropped_once_its_original_is_gone(self, store):
        _store_guidelines(store, A="x", B="x")
        _consolidate(store, {"x"}, undeletable={"A"})
        _store_guidelines(store, C="x")

        # The hold is lifted: this run deletes A first, then merges A+B with C.
        _, stored, _ = _consolidate(store, {"x"})

        assert stored == {"A+B+C": 3}
        merged_from = _stored(store)["A+B+C"].metadata["merged_from"]
        assert len(merged_from) == 2  # A+B and C, not A
