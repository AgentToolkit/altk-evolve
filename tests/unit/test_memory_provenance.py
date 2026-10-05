"""Reconciliation scope and source associations through real filesystem writes."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from altk_evolve.backend.filesystem import FilesystemEntityBackend
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.schema.core import Entity, RecordedEntity
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.retention.collection import Collection

pytestmark = pytest.mark.unit


@pytest.fixture
def backend(tmp_path):
    instance = FilesystemEntityBackend(FilesystemSettings(data_dir=str(tmp_path)))
    instance.create_namespace("n")
    return instance


def fact(conversation, user="alice", agent="a"):
    return Entity(type="fact", content="preferred style", metadata={"user_id": user, "agent_id": agent, "thread_id": conversation})


def test_unchanged_none_candidates_do_not_conflict_across_batches(backend):
    original = backend.update_entities(
        "n", [Entity(type="fact", content="unrelated", metadata={"sources": [], "provenance_incomplete": True})], False
    )[0]

    def reconcile(old, new):
        return [
            EntityUpdate(id=old[0].id, type="fact", content=old[0].content, event="NONE"),
            EntityUpdate(id=new[0].id, type="fact", content=new[0].content, event="ADD"),
        ]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        batches = [backend.prepare_updates("n", [Entity(type="fact", content="unrelated")]) for _ in range(2)]
    assert all(original.id not in batch.expected for batch in batches)
    with patch.object(backend, "_reaffirm_entity", wraps=backend._reaffirm_entity) as reaffirm:
        backend.commit_prepared("n", batches, checkpoint=("batch", {}))
        reaffirm.assert_not_called()
    assert len(backend.search_entities("n")) == 3


def test_candidates_are_isolated_and_none_records_a_second_source(backend):
    for user, agent in [("alice", "a"), ("bob", "a"), ("alice", "b")]:
        backend.update_entities("n", [fact("first", user, agent)], False)

    def reconcile(old, new):
        assert len(old) == 1
        assert old[0].metadata["user_id"] == "alice"
        assert old[0].metadata["agent_id"] == "a"
        return [EntityUpdate(id=old[0].id, type="fact", content=old[0].content, event="NONE", incoming_ids=[new[0].id])]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        result = backend.update_entities("n", [fact("second")])
    stored = backend.search_entities("n", filters={"id": result[0].id})[0]
    assert {source["conversation_id"] for source in stored.metadata["sources"]} == {"first", "second"}


def test_correction_supersedes_old_source_and_keeps_identity(backend):
    backend.update_entities("n", [fact("first")], False)

    def reconcile(old, new):
        return [
            EntityUpdate(
                id=old[0].id,
                type="fact",
                content="new preference",
                event="UPDATE",
                incoming_ids=[new[0].id],
                supersedes=True,
                metadata={"user_id": "bob"},
            )
        ]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        result = backend.update_entities("n", [fact("second")])
    stored = backend.search_entities("n", filters={"id": result[0].id})[0]
    assert stored.metadata["user_id"] == "alice"
    assert [(s["conversation_id"], s["status"]) for s in stored.metadata["sources"]] == [("first", "superseded"), ("second", "supporting")]


def test_model_cannot_write_another_users_id(backend):
    foreign = backend.update_entities("n", [fact("first", "bob")], False)[0].id
    with patch(
        "altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts",
        return_value=[EntityUpdate(id=foreign, type="fact", content="bad", event="UPDATE")],
    ):
        with pytest.raises(EvolveException, match="out-of-scope"):
            backend.update_entities("n", [fact("second")])
    assert backend.search_entities("n", filters={"id": foreign})[0].content == "preferred style"


def test_all_supporting_sources_must_have_receipts():
    now = datetime.now(UTC)
    entity = RecordedEntity(**fact("first").model_dump(), id="1", created_at=now - timedelta(days=30))
    entity.metadata["sources"] = [dict(conversation_id=c, user_id="alice", agent_id="a", status="supporting") for c in ["first", "second"]]
    receipts = {"first": now - timedelta(days=10)}

    class Connection:
        def execute(self, sql, values):
            return SimpleNamespace(
                fetchall=lambda: [
                    {"user_id": "alice", "agent_id": "a", "source_id": source, "deleted_at": when} for source, when in receipts.items()
                ]
            )

    collection = object.__new__(Collection)
    collection.namespace = "n"
    assert collection.source_deletion_times(Connection(), [entity]) == {}
    receipts["second"] = now - timedelta(days=1)
    assert collection.source_deletion_times(Connection(), [entity]) == {"1": receipts["second"]}
    entity.metadata["provenance_incomplete"] = True
    assert collection.source_deletion_times(Connection(), [entity]) == {}


def test_independent_filesystem_clients_preserve_concurrent_sources(backend):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    backend.update_entities("n", [fact("original")], False)
    other = FilesystemEntityBackend(backend.config)

    barrier = Barrier(2)
    lock = Lock()
    calls = 0

    def reconcile(old, new):
        nonlocal calls
        assert not backend.in_transaction and not other.in_transaction
        with lock:
            calls += 1
            wait = calls <= 2
        if wait:
            barrier.wait(timeout=10)
        return [EntityUpdate(id=old[0].id, type="fact", content=old[0].content, event="NONE", incoming_ids=[new[0].id])]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(client.update_entities, "n", [fact(source)]) for client, source in [(backend, "one"), (other, "two")]]
            for result in results:
                result.result(timeout=10)
    stored = backend.search_entities("n")[0]
    assert {s["conversation_id"] for s in stored.metadata["sources"]} == {"original", "one", "two"}


def test_unknown_associations_do_not_claim_complete_provenance(backend):
    backend.update_entities("n", [fact("first")], False)

    def reconcile(old, new):
        return [EntityUpdate(id=old[0].id, type="fact", content="merged wording", event="UPDATE")]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        backend.update_entities("n", [fact("second")])
    assert backend.search_entities("n")[0].metadata["provenance_incomplete"]


@pytest.mark.parametrize("event", ["NONE", "UPDATE"])
def test_prepared_sources_commit_with_checkpoint_or_roll_back(backend, monkeypatch, event):
    original = backend.update_entities("n", [fact("first")], False)[0]
    before = backend.scan_entities("n")
    settings = object()

    def reconcile(old, new, **kwargs):
        assert kwargs["settings"] is settings
        assert not backend.in_transaction
        return [
            EntityUpdate(
                id=old[0].id,
                type="fact",
                content="corrected" if event == "UPDATE" else old[0].content,
                event=event,
                incoming_ids=[new[0].id],
            )
        ]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        prepared = backend.prepare_updates(
            "n", [fact("second")], conflict_settings=settings, processing_provenance={"processor_id": "facts"}
        )
    assert backend.scan_entities("n") == before
    save_checkpoint = backend._save_processing_checkpoint

    def fail_checkpoint(*args):
        raise OSError("checkpoint failed")

    monkeypatch.setattr(backend, "_save_processing_checkpoint", fail_checkpoint)
    with pytest.raises(OSError, match="checkpoint failed"):
        backend.commit_prepared("n", [prepared], checkpoint=("batch", {"processor_id": "facts"}))
    assert backend.scan_entities("n") == before
    assert backend.get_processing_checkpoint("n", "batch") is None
    monkeypatch.setattr(backend, "_save_processing_checkpoint", save_checkpoint)
    backend.commit_prepared("n", [prepared], checkpoint=("batch", {"processor_id": "facts"}))
    stored = backend.scan_entities("n")[0]
    assert stored.id == original.id
    assert {s["conversation_id"] for s in stored.metadata["sources"]} == {"first", "second"}
    assert stored.metadata["memory_revision"] == (2 if event == "UPDATE" else 1)
    if event == "UPDATE":
        assert stored.metadata["processing"] == {"processor_id": "facts"}
    else:
        assert stored.created_at == before[0].created_at
    assert backend.commit_prepared("n", [prepared], checkpoint=("batch", {})) is None
    assert backend.scan_entities("n") == [stored]


def test_stale_prepared_sources_do_not_commit_checkpoint(backend):
    from altk_evolve.backend.base import ConcurrentEntityUpdate

    backend.update_entities("n", [fact("first")], False)

    def reconcile(old, new):
        return [EntityUpdate(id=old[0].id, type="fact", content=old[0].content, event="NONE", incoming_ids=[new[0].id])]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        first = backend.prepare_updates("n", [fact("second")])
        stale = backend.prepare_updates("n", [fact("third")])
        backend.commit_prepared("n", [first], checkpoint=("one", {}))
        with pytest.raises(ConcurrentEntityUpdate):
            backend.commit_prepared("n", [stale], checkpoint=("two", {}))
        assert backend.get_processing_checkpoint("n", "two") is None
        fresh = backend.prepare_updates("n", [fact("third")])
        backend.commit_prepared("n", [fresh], checkpoint=("two", {}))
    assert {s["conversation_id"] for s in backend.scan_entities("n")[0].metadata["sources"]} == {"first", "second", "third"}


def test_add_preserves_all_explicit_sources_and_rejects_unknown_ids(backend):
    def reconcile(old, new):
        return [EntityUpdate(id=new[0].id, type="fact", content="combined", event="ADD", incoming_ids=[new[1].id])]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        backend.update_entities("n", [fact("one"), fact("two")])
    assert {s["conversation_id"] for s in backend.scan_entities("n")[0].metadata["sources"]} == {"one", "two"}

    def invalid(old, new):
        return [EntityUpdate(id=new[0].id, type="fact", content="bad", event="ADD", incoming_ids=["unknown"])]

    before = backend.scan_entities("n")
    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=invalid):
        with pytest.raises(EvolveException, match="unknown source ID"):
            backend.update_entities("n", [fact("three")])
    assert backend.scan_entities("n") == before


def test_source_only_batches_cannot_overwrite_each_other(backend):
    backend.update_entities("n", [fact("original")], False)

    def reconcile(old, new):
        return [EntityUpdate(id=old[0].id, type="fact", content=old[0].content, event="NONE", incoming_ids=[new[0].id])]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        batches = [backend.prepare_updates("n", [fact(source)]) for source in ["one", "two"]]
    with pytest.raises(EvolveException, match="Multiple prepared mutations"):
        backend.commit_prepared("n", batches, checkpoint=("batch", {}))
    assert backend.get_processing_checkpoint("n", "batch") is None
    assert len(backend.scan_entities("n")[0].metadata["sources"]) == 1


def test_source_reaffirmation_preserves_newer_access_stamp(backend):
    backend.update_entities("n", [fact("original")], False)
    entity_id = backend.scan_entities("n")[0].id
    backend.update_entity_metadata("n", entity_id, {"last_accessed": "2026-01-01"})

    def reconcile(old, new):
        return [EntityUpdate(id=old[0].id, type="fact", content=old[0].content, event="NONE", incoming_ids=[new[0].id])]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        prepared = backend.prepare_updates("n", [fact("second")])
    backend.update_entity_metadata("n", entity_id, {"last_accessed": "2026-02-01"})
    backend.commit_prepared("n", [prepared])
    stored = backend.scan_entities("n")[0]
    assert stored.metadata["last_accessed"] == "2026-02-01"
    assert len(stored.metadata["sources"]) == 2


def test_source_times_survive_content_updates_but_refresh_on_reaffirmation(backend):
    backend.update_entities("n", [fact("original")], False)
    old = backend.scan_entities("n")[0]
    original_time = old.metadata["sources"][0]["associated_at"]

    def reconcile(stored, incoming):
        return [EntityUpdate(id=stored[0].id, type="fact", content=stored[0].content, event="UPDATE", incoming_ids=[incoming[0].id])]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        backend.update_entities("n", [fact("second")])
    sources = backend.scan_entities("n")[0].metadata["sources"]
    assert sources[0]["associated_at"] == original_time
    assert sources[1]["associated_at"] > original_time
    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", side_effect=reconcile):
        backend.update_entities("n", [fact("original")])
    sources = backend.scan_entities("n")[0].metadata["sources"]
    assert next(s for s in sources if s["conversation_id"] == "original")["associated_at"] > original_time


# ---------------------------------------------------------------------------
# Consolidation keeps source associations
# ---------------------------------------------------------------------------


def guideline(content, conversation=None, user="alice", agent="a", task="handle errors"):
    metadata = {"task_description": task, "rationale": "r", "category": "strategy", "trigger": "t", "user_id": user, "agent_id": agent}
    if conversation is not None:
        metadata["thread_id"] = conversation
    return Entity(type="guideline", content=content, metadata=metadata)


def merge_response(*groups):
    """A mocked merge-LLM reply: one consolidated guideline per group of input indices."""
    body = {
        "guidelines": [
            {"content": f"merged {group}", "rationale": "", "category": "strategy", "trigger": "", "source_indices": group}
            for group in groups
        ]
    }
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))])


def consolidate(backend, *groups):
    """Run EvolveClient.consolidate_guidelines over the stored guidelines as one cluster per scope."""
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    client = EvolveClient.__new__(EvolveClient)
    client.backend = backend
    client.config = SimpleNamespace(clustering_threshold=0.8, consolidation_mode="lossless")
    with (
        patch("altk_evolve.llm.guidelines.clustering.cluster_entities", side_effect=lambda entities, threshold: [entities]),
        patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[]),
        patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False),
        patch("altk_evolve.llm.guidelines.clustering.completion", return_value=merge_response(*groups)),
    ):
        return client.consolidate_guidelines("n")


def stored_guidelines(backend):
    return {entity.content: entity for entity in backend.scan_entities("n", filters={"type": "guideline"})}


def conversations(entity):
    return {source["conversation_id"] for source in entity.metadata.get("sources", [])}


def test_consolidation_keeps_every_members_sources_with_original_times(backend):
    backend.update_entities("n", [guideline("A", "one"), guideline("B", "two"), guideline("C", "three")], False)
    original = {s["conversation_id"]: s for e in backend.scan_entities("n") for s in e.metadata["sources"]}

    consolidate(backend, [0, 1, 2])

    (merged,) = stored_guidelines(backend).values()
    assert conversations(merged) == {"one", "two", "three"}
    assert merged.metadata["provenance_incomplete"] is False
    # Consolidation is not a new observation: each association keeps the time it was
    # first made, so a deletion receipt recorded before consolidation still applies.
    assert {s["conversation_id"]: s["associated_at"] for s in merged.metadata["sources"]} == {
        conversation: source["associated_at"] for conversation, source in original.items()
    }


def test_each_member_source_lands_on_exactly_one_consolidated_guideline(backend):
    backend.update_entities("n", [guideline(c.upper(), c) for c in ["a", "b", "c", "d"]], False)
    order = [e.metadata["thread_id"] for e in backend.scan_entities("n", filters={"type": "guideline"})]

    # Index 1 is claimed twice; the second claim is ignored. Index 3 is never claimed.
    consolidate(backend, [0, 1], [1, 2])

    stored = stored_guidelines(backend)
    assert conversations(stored["merged [0, 1]"]) == {order[0], order[1]}
    assert conversations(stored["merged [1, 2]"]) == {order[2]}
    # The uncovered member is carried through unchanged, with its own source.
    assert conversations(stored[order[3].upper()]) == {order[3]}
    claimed = [c for entity in stored.values() for c in conversations(entity)]
    assert sorted(claimed) == sorted(order)


def test_consolidated_guideline_without_attributed_members_is_not_written(backend):
    backend.update_entities("n", [guideline("A", "one"), guideline("B", "two")], False)

    consolidate(backend, [], [7], [0, 1])

    (merged,) = stored_guidelines(backend).values()
    assert merged.content == "merged [0, 1]"
    assert conversations(merged) == {"one", "two"}


def test_consolidation_marks_partial_provenance_incomplete(backend):
    backend.update_entities("n", [guideline("A", "one"), guideline("B")], False)

    consolidate(backend, [0, 1])

    (merged,) = stored_guidelines(backend).values()
    assert conversations(merged) == {"one"}
    assert merged.metadata["provenance_incomplete"] is True


def test_consolidation_of_members_without_provenance_adds_none(backend):
    backend.update_entities("n", [guideline("A"), guideline("B")], False)

    consolidate(backend, [0, 1])

    (merged,) = stored_guidelines(backend).values()
    assert "sources" not in merged.metadata
    assert "provenance_incomplete" not in merged.metadata
