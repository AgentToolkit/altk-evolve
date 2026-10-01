"""Reconciliation rules operate on independent snapshots without backend services."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from altk_evolve.backend.reconciliation import reconcile_decisions, attach_processing_provenance, prepare_additions
from altk_evolve.schema.core import Entity, RecordedEntity
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.exceptions import EvolveException

pytestmark = pytest.mark.unit
OBSERVED = datetime(2026, 9, 30, tzinfo=UTC)


@pytest.mark.parametrize("references", [[], ["incoming"], ["incoming", "incoming"]])
def test_discarded_incoming_can_reference_itself(references):
    incoming = [RecordedEntity(id="incoming", type="fact", content="discard", created_at=OBSERVED)]
    decision = EntityUpdate(id="incoming", type="fact", content="discard", event="NONE", incoming_ids=references)
    assert reconcile_decisions(incoming, {}, [decision], "fact", OBSERVED) == []


def test_discarded_incoming_cannot_claim_another_source():
    incoming = [RecordedEntity(id=identifier, type="fact", content="discard", created_at=OBSERVED) for identifier in ["one", "two"]]
    decision = EntityUpdate(id="one", type="fact", content="discard", event="NONE", incoming_ids=["two"])
    with pytest.raises(EvolveException, match="attached sources to a discarded"):
        reconcile_decisions(incoming, {}, [decision], "fact", OBSERVED)


@pytest.mark.parametrize("event", ["ADD", "UPDATE", "NONE", "DELETE"])
def test_reconciliation_is_deterministic_and_does_not_alias_inputs(event):
    incoming = [
        RecordedEntity(
            id="incoming",
            type="fact",
            content={"value": ["new"]},
            created_at=OBSERVED,
            metadata={"user_id": "alice", "thread_id": "second"},
        )
    ]
    stored = {
        "stored": RecordedEntity(
            id="stored",
            type="fact",
            content={"value": ["old"]},
            created_at=OBSERVED,
            metadata={"user_id": "alice", "thread_id": "first", "legal_hold": True},
        )
    }
    decisions = [
        EntityUpdate(
            id="incoming" if event == "ADD" else "stored",
            type="fact",
            content={"value": ["model"]},
            event=event,
            incoming_ids=["incoming"],
            metadata={"user_id": "bob"},
        )
    ]
    original = deepcopy((incoming, stored, decisions))
    changes = reconcile_decisions(incoming, stored, decisions, "fact", OBSERVED)
    assert changes == reconcile_decisions(incoming, stored, decisions, "fact", OBSERVED)
    assert (incoming, stored, decisions) == original
    assert changes[0].metadata["user_id"] == "alice"
    assert changes[0].metadata["sources"][-1]["associated_at"] == OBSERVED.isoformat()
    if event != "ADD":
        assert changes[0].metadata["legal_hold"] is True
    if event == "NONE":
        assert changes[0].content == stored["stored"].content
    changes[0].content["value"].append("mutated")
    changes[0].metadata["sources"][0]["conversation_id"] = "mutated"
    changes[0].incoming_ids.append("mutated")
    assert (incoming, stored, decisions) == original


def test_processing_provenance_preserves_history_without_aliasing():
    stored = {
        "stored": RecordedEntity(
            id="stored",
            type="fact",
            content="fact",
            created_at=OBSERVED,
            metadata={"processing": {"revision": 1}, "processing_history": [{"revision": 0}]},
        )
    }
    changes = [EntityUpdate(id="stored", type="fact", content="updated", event="UPDATE")]
    provenance = {"revision": 2, "plugins": ["facts"]}
    original = deepcopy((stored, changes, provenance))
    result = attach_processing_provenance(changes, stored, provenance)
    assert result[0].metadata["processing_history"] == [{"revision": 0}, {"revision": 1}]
    result[0].metadata["processing_history"][0]["revision"] = 99
    result[0].metadata["processing"]["plugins"].append("other")
    assert (stored, changes, provenance) == original


def test_append_path_is_deterministic_and_copies_nested_content():
    entities = [Entity(type="fact", content={"value": []}, metadata={"thread_id": "conversation"})]
    result = prepare_additions(entities, "fact", OBSERVED)
    assert result == prepare_additions(entities, "fact", OBSERVED)
    assert result[0].metadata["memory_revision"] == 1
    result[0].content["value"].append("mutation")
    assert entities[0].content == {"value": []}
