"""Reconciliation scope and source associations through real filesystem writes."""

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
            value = receipts.get(values[3])
            return SimpleNamespace(fetchone=lambda: {"deleted_at": value} if value else None)

    collection = object.__new__(Collection)
    collection.namespace = "n"
    assert collection.source_deletion_times(Connection(), [entity]) == {}
    receipts["second"] = now - timedelta(days=1)
    assert collection.source_deletion_times(Connection(), [entity]) == {"1": receipts["second"]}
    entity.metadata["provenance_incomplete"] = True
    assert collection.source_deletion_times(Connection(), [entity]) == {}


def test_independent_filesystem_clients_preserve_concurrent_sources(backend):
    from concurrent.futures import ThreadPoolExecutor

    backend.update_entities("n", [fact("original")], False)
    other = FilesystemEntityBackend(backend.config)

    def reconcile(old, new):
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
