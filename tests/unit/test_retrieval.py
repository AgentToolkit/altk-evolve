"""Unit tests for dosage-aware guideline selection (core + top-k retrieval)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from altk_evolve.llm.guidelines.retrieval import GuidelineSelection, format_selection, select_guidelines
from altk_evolve.schema.core import RecordedEntity


def _make_entity(
    entity_id: str,
    content: str,
    task_description: str = "",
    support: int = 1,
    evidence: str | None = None,
) -> RecordedEntity:
    metadata: dict = {"support": support}
    if task_description:
        metadata["task_description"] = task_description
    if evidence is not None:
        metadata["evidence"] = evidence
    return RecordedEntity(
        id=entity_id,
        content=content,
        type="guideline",
        metadata=metadata,
        created_at=datetime(2025, 1, 1),
    )


def _vec(text: str) -> list[float]:
    """Deterministic orthogonal-ish embedding keyed on a marker word in the text."""
    t = text.lower()
    if "spotify" in t:
        return [1.0, 0.0, 0.0]
    if "venmo" in t:
        return [0.0, 1.0, 0.0]
    if "email" in t:
        return [0.0, 0.0, 1.0]
    return [0.5, 0.5, 0.0]


def _mock_encode(texts, normalize_embeddings=True):
    return np.array([_vec(t) for t in texts])


@pytest.mark.unit
@patch("altk_evolve.llm.guidelines.retrieval._get_sentence_transformer")
class TestSelectGuidelines:
    def _model(self, mock_st):
        model = MagicMock()
        model.encode = _mock_encode
        mock_st.return_value = model
        return model

    def test_core_always_included_regardless_of_similarity(self, mock_st):
        self._model(mock_st)
        # Core (support 3) is about email; task is about spotify -> dissimilar, still included.
        core = _make_entity("c", "Handle email pagination", task_description="email task", support=3)
        cand = _make_entity("s", "Spotify search tip", task_description="spotify task", support=1)

        sel = select_guidelines([core, cand], "do something on spotify", top_k=5, core_support=3, embedding_model="test")

        assert [e.id for e in sel.core] == ["c"]
        assert [e.id for e in sel.retrieved] == ["s"]

    def test_topk_ranked_by_source_task_similarity(self, mock_st):
        self._model(mock_st)
        cands = [
            _make_entity("spotify", "Spotify rule", task_description="a spotify task", support=1),
            _make_entity("venmo", "Venmo rule", task_description="a venmo task", support=1),
            _make_entity("email", "Email rule", task_description="an email task", support=1),
        ]
        sel = select_guidelines(cands, "pay someone on venmo", top_k=1, core_support=3, embedding_model="test")

        assert sel.core == []
        assert [e.id for e in sel.retrieved] == ["venmo"]

    def test_min_support_filters_candidate_pool(self, mock_st):
        self._model(mock_st)
        cands = [
            _make_entity("weak", "Venmo weak", task_description="a venmo task", support=1),
            _make_entity("strong", "Venmo strong", task_description="a venmo task", support=2),
        ]
        sel = select_guidelines(cands, "pay on venmo", top_k=5, core_support=3, min_support=2, embedding_model="test")

        assert [e.id for e in sel.retrieved] == ["strong"]

    def test_near_core_candidate_dropped(self, mock_st):
        self._model(mock_st)
        # Candidate content duplicates the core content -> dropped as already covered.
        core = _make_entity("c", "Venmo rule", task_description="venmo core", support=3)
        dup = _make_entity("d", "Venmo rule", task_description="a venmo task", support=1)
        fresh = _make_entity("f", "Email rule", task_description="an email task", support=1)

        sel = select_guidelines([core, dup, fresh], "venmo payment", top_k=5, core_support=3, near_core_thresh=0.9, embedding_model="test")

        assert [e.id for e in sel.core] == ["c"]
        assert "d" not in {e.id for e in sel.retrieved}
        assert "f" in {e.id for e in sel.retrieved}

    def test_dedup_drops_near_identical_candidate(self, mock_st):
        self._model(mock_st)
        # v1 and v2 have identical content (same embedding) -> dedup keeps one; email is distinct.
        v1 = _make_entity("v1", "Venmo rule", task_description="a venmo task", support=1)
        v2 = _make_entity("v2", "Venmo rule", task_description="another venmo task", support=1)
        email = _make_entity("email", "Email rule", task_description="an email task", support=1)

        sel = select_guidelines([v1, v2, email], "venmo payment", top_k=5, core_support=3, dedup_thresh=0.9, embedding_model="test")

        retrieved_ids = {e.id for e in sel.retrieved}
        assert len(retrieved_ids & {"v1", "v2"}) == 1  # exactly one of the duplicates survives
        assert "email" in retrieved_ids
        assert len(sel.retrieved) == 2

    def test_topk_zero_returns_core_only(self, mock_st):
        self._model(mock_st)
        core = _make_entity("c", "Email rule", task_description="email", support=3)
        cand = _make_entity("s", "Spotify rule", task_description="spotify", support=1)
        sel = select_guidelines([core, cand], "spotify task", top_k=0, core_support=3, embedding_model="test")
        assert [e.id for e in sel.core] == ["c"]
        assert sel.retrieved == []

    def test_no_candidates_returns_core_only(self, mock_st):
        self._model(mock_st)
        core = _make_entity("c", "Email rule", task_description="email", support=3)
        sel = select_guidelines([core], "email task", top_k=5, core_support=3, embedding_model="test")
        assert [e.id for e in sel.core] == ["c"]
        assert sel.retrieved == []


@pytest.mark.unit
def test_format_selection_renders_core_and_retrieved():
    core = [_make_entity("c", "Always paginate", support=3)]
    retrieved = [_make_entity("r", "Use exact match search", support=1)]
    text = format_selection(GuidelineSelection(core=core, retrieved=retrieved))
    assert "Always paginate" in text
    assert "Additional task-specific guidelines" in text
    assert "Use exact match search" in text


@pytest.mark.unit
class TestClientSelectGuidelines:
    def test_select_guidelines_filters_evidence_and_delegates(self):
        from altk_evolve.frontend.client.evolve_client import EvolveClient

        entities = [
            _make_entity("ok", "keep me", support=1, evidence="failure"),
            _make_entity("drop", "drop me", support=1, evidence="success"),
            _make_entity("unknown", "keep unknown", support=1),
        ]
        mock_backend = MagicMock()
        client = EvolveClient.__new__(EvolveClient)
        client.backend = mock_backend
        client.config = MagicMock()
        client.config.retrieval_top_k = 5
        client.config.core_support = 3
        client.config.min_support = 1
        client.config.evidence_filter = "failure"
        client.config.retrieval_similarity_key = "source_task"
        client.config.retrieval_near_core_thresh = 0.75
        client.config.retrieval_dedup_thresh = 0.90

        captured = {}

        def fake_select(ents, task_query, **kwargs):
            captured["ids"] = [e.id for e in ents]
            return GuidelineSelection(core=list(ents), retrieved=[])

        with (
            patch.object(client, "get_all_entities", return_value=entities),
            patch("altk_evolve.llm.guidelines.retrieval.select_guidelines", side_effect=fake_select),
        ):
            client.select_guidelines("ns", "some task")

        # success-evidence guideline dropped; failure + unknown kept.
        assert captured["ids"] == ["ok", "unknown"]

    def test_select_guidelines_fetches_full_unlimited_pool(self):
        """Guards against a silent cap: the candidate pool must not be limited to the
        get_all_entities default (100), or retrieval would ignore most guidelines."""
        from altk_evolve.frontend.client.evolve_client import EvolveClient

        client = EvolveClient.__new__(EvolveClient)
        client.backend = MagicMock()
        client.config = MagicMock()
        client.config.retrieval_top_k = 5
        client.config.core_support = 3
        client.config.min_support = 1
        client.config.evidence_filter = "all"
        client.config.retrieval_similarity_key = "source_task"
        client.config.retrieval_near_core_thresh = 0.75
        client.config.retrieval_dedup_thresh = 0.90

        with patch.object(client, "get_all_entities", return_value=[]) as mock_get:
            client.select_guidelines("ns", "some task")

        mock_get.assert_called_once_with("ns", filters={"type": "guideline"}, limit=10000)


@pytest.mark.unit
class TestInjectionModeRouting:
    """get_guidelines must honor EvolveConfig.injection_mode (default 'static')."""

    def test_static_mode_uses_full_playbook_path(self):
        import altk_evolve.config.evolve as cfg
        import altk_evolve.frontend.mcp.mcp_server as mcp

        with (
            patch.object(cfg.evolve_config, "injection_mode", "static"),
            patch.object(mcp, "get_entities_logic", return_value="STATIC") as static_path,
            patch.object(mcp, "get_relevant_guidelines", return_value="RETRIEVAL") as retrieval_path,
        ):
            out = mcp.get_guidelines("do a task")

        assert out == "STATIC"
        static_path.assert_called_once()
        retrieval_path.assert_not_called()

    def test_retrieval_mode_routes_through_dosage_path(self):
        import altk_evolve.config.evolve as cfg
        import altk_evolve.frontend.mcp.mcp_server as mcp

        with (
            patch.object(cfg.evolve_config, "injection_mode", "retrieval"),
            patch.object(mcp, "get_entities_logic", return_value="STATIC") as static_path,
            patch.object(mcp, "get_relevant_guidelines", return_value="RETRIEVAL") as retrieval_path,
        ):
            out = mcp.get_guidelines("do a task")

        assert out == "RETRIEVAL"
        retrieval_path.assert_called_once()
        static_path.assert_not_called()
