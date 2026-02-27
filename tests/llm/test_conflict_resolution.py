"""LLM-based tests for conflict resolution.

These tests call the real LLM and verify that the conflict resolution prompt
produces semantically correct diffs. They are slow and require a configured
LLM backend, so they are marked `llm` and excluded from the default run.

Run with: uv run pytest -m llm
"""

from datetime import datetime

import pytest

from kaizen.llm.conflict_resolution.conflict_resolution import resolve_conflicts
from kaizen.schema.core import RecordedEntity


@pytest.mark.llm
def test_add_to_empty_store():
    """All incoming entities should be ADDed when the store is empty."""
    new = [
        RecordedEntity(
            id="g1",
            type="guideline",
            content="Always use type hints in Python function signatures.",
            metadata={},
            created_at=datetime.now(),
        ),
        RecordedEntity(
            id="g2", type="guideline", content="Prefer f-strings over .format() or % formatting.", metadata={}, created_at=datetime.now()
        ),
    ]
    updates = {u.id: u for u in resolve_conflicts([], new)}
    assert updates["g1"].event == "ADD"
    assert updates["g2"].event == "ADD"


@pytest.mark.llm
def test_none_for_duplicate_and_equivalent():
    """Exact duplicates and semantic paraphrases should not produce new ADDs."""
    old = [
        RecordedEntity(
            id="g1", type="fact", content="Always use type hints in Python function signatures.", metadata={}, created_at=datetime.now()
        ),
        RecordedEntity(id="g2", type="fact", content="Likes cheese pizza", metadata={}, created_at=datetime.now()),
    ]
    new = [
        # Exact duplicate
        RecordedEntity(
            id="g1_dup", type="fact", content="Always use type hints in Python function signatures.", metadata={}, created_at=datetime.now()
        ),
        # Semantic paraphrase — same meaning, slightly different wording
        RecordedEntity(id="g2_dup", type="fact", content="Loves cheese pizza", metadata={}, created_at=datetime.now()),
    ]
    updates = {u.id: u for u in resolve_conflicts(old, new)}
    assert updates["g1"].event == "NONE"
    assert updates["g2"].event == "NONE"
    for u in updates.values():
        assert u.event != "ADD"


@pytest.mark.llm
def test_update_preserves_id_and_captures_old_content():
    """An enriched incoming entity should UPDATE the existing one, keeping its ID and recording old_entity."""
    old = [
        RecordedEntity(id="g1", type="fact", content="User likes to play cricket", metadata={}, created_at=datetime.now()),
        RecordedEntity(id="g2", type="fact", content="User is a software engineer", metadata={}, created_at=datetime.now()),
    ]
    new = [
        # Richer version of g1
        RecordedEntity(
            id="n1", type="fact", content="Loves to play cricket with friends on weekends", metadata={}, created_at=datetime.now()
        ),
    ]
    updates = {u.id: u for u in resolve_conflicts(old, new)}
    assert updates["g1"].event == "UPDATE"
    assert updates["g1"].id == "g1"  # ID must not change
    assert updates["g1"].old_entity is not None  # old content must be recorded
    assert "cricket" in updates["g1"].old_entity
    assert updates["g2"].event == "NONE"


@pytest.mark.llm
def test_delete_contradicted_fact():
    """A directly contradicting incoming entity should DELETE the old one."""
    old = [
        RecordedEntity(id="g1", type="fact", content="Name is John", metadata={}, created_at=datetime.now()),
        RecordedEntity(id="g2", type="fact", content="Loves cheese pizza", metadata={}, created_at=datetime.now()),
    ]
    new = [RecordedEntity(id="n1", type="fact", content="Dislikes cheese pizza", metadata={}, created_at=datetime.now())]
    updates = {u.id: u for u in resolve_conflicts(old, new)}
    assert updates["g2"].event == "DELETE"
    assert updates["g1"].event == "NONE"


@pytest.mark.llm
def test_mixed_add_update_delete_none():
    """A realistic batch: ADD new info, UPDATE enriched info, DELETE contradicted info, NONE for unchanged."""
    old = [
        RecordedEntity(id="g1", type="fact", content="I really like cheese pizza", metadata={}, created_at=datetime.now()),
        RecordedEntity(id="g2", type="fact", content="User is a software engineer", metadata={}, created_at=datetime.now()),
        RecordedEntity(id="g3", type="fact", content="User likes to play cricket", metadata={}, created_at=datetime.now()),
    ]
    new = [
        RecordedEntity(
            id="n1", type="fact", content="Loves chicken pizza", metadata={}, created_at=datetime.now()
        ),  # contradicts / updates g1
        RecordedEntity(
            id="n2", type="fact", content="Loves to play cricket with friends", metadata={}, created_at=datetime.now()
        ),  # enriches g3
        RecordedEntity(id="n3", type="fact", content="Name is John", metadata={}, created_at=datetime.now()),  # brand new
    ]
    updates = {u.id: u for u in resolve_conflicts(old, new)}
    assert updates["g1"].event == "UPDATE"
    assert updates["g2"].event == "NONE"
    assert updates["g3"].event == "UPDATE"
    assert updates["n3"].event == "ADD"
