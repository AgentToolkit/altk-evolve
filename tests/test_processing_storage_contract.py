"""The same concurrency contract runs against JSON storage and real PostgreSQL."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from altk_evolve.backend.base import ConcurrentEntityUpdate
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import Entity


@pytest.fixture(params=[pytest.param("filesystem", marks=pytest.mark.unit), pytest.param("postgres", marks=pytest.mark.e2e)])
def storage(request, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    monkeypatch.setenv("EVOLVE_SQLITE_PATH", str(tmp_path / "metadata.db"))
    if request.param == "filesystem":
        config = EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path / "entities")))
    else:
        from psycopg.conninfo import conninfo_to_dict
        from altk_evolve.config.postgres import PostgresDBSettings
        import numpy as np

        dsn = os.getenv("EVOLVE_TEST_SCHEDULE_POSTGRES_DSN")
        if not dsn:
            pytest.skip("Set EVOLVE_TEST_SCHEDULE_POSTGRES_DSN to a disposable pgvector database")

        class Embeddings:
            def get_sentence_embedding_dimension(self):
                return 3

            def encode(self, content):
                return np.array([1.0, 0.0, 0.0])

        monkeypatch.setattr("altk_evolve.backend.postgres.SentenceTransformer", lambda _: Embeddings())
        settings = PostgresDBSettings(**conninfo_to_dict(dsn))
        config = EvolveConfig(backend="postgres", settings=settings)
    client, peer = EvolveClient(config), EvolveClient(config)
    namespace = "contract_" + uuid.uuid4().hex + "-quoted.test"
    client.create_namespace(namespace)
    client.update_entities(namespace, [Entity(type="note", content="seed")], enable_conflict_resolution=False)
    try:
        yield client, peer, namespace
    finally:
        client.delete_namespace(namespace)
        client.backend.close()
        peer.backend.close()


def source_batch(batch_id="event-1", **kwargs):
    return {"source": "test", "conversation_id": "ongoing", "batch_id": batch_id, **kwargs}


def install_processor(client, callback=None):
    from pydantic import BaseModel
    from altk_evolve.processing import ProcessorResult

    class Config(BaseModel):
        label: str = "first"

    class Processor:
        id = "test.incremental"
        api_version = 1
        version = "1"
        config_model = Config

        @classmethod
        def from_config(cls, config):
            result = cls()
            result.config = config
            return result

        def process(self, trajectory, *, context):
            if callback:
                callback(trajectory, self.config)
            return ProcessorResult(entities=[Entity(type="note", content=self.config.label)])

    client.processing.registry.register(Processor)
    return {"processors": [{"id": "learner", "plugin": Processor.id}]}


def test_unrelated_write_during_reconciliation_does_not_retry(storage, monkeypatch):
    from altk_evolve.schema.conflict_resolution import EntityUpdate

    client, peer, ns = storage
    calls = []

    def resolve(old, new, **kwargs):
        calls.append(1)
        with ThreadPoolExecutor(1) as pool:
            pool.submit(peer.update_entities, ns, [Entity(type="note", content="concurrent")], False).result(timeout=5)
        return [EntityUpdate(id="new", type="note", content="learned", event="ADD")]

    monkeypatch.setattr("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", resolve)
    prepared = client.backend.prepare_updates(ns, [Entity(type="note", content="seed")])
    client.backend.commit_prepared(ns, [prepared], checkpoint=("batch", {"done": True}))
    assert calls == [1]
    assert {e.content for e in peer.backend.scan_entities(ns)} == {"seed", "concurrent", "learned"}
    assert peer.backend.get_processing_checkpoint(ns, "batch") == {"done": True}


def test_only_changed_destructive_target_rejects_commit(storage, monkeypatch):
    from altk_evolve.schema.conflict_resolution import EntityUpdate

    client, peer, ns = storage
    seed = client.backend.scan_entities(ns)[0]
    monkeypatch.setattr(
        "altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts",
        lambda *a, **kw: [EntityUpdate(id=seed.id, type="note", content="replacement", event="UPDATE")],
    )
    prepared = client.backend.prepare_updates(ns, [Entity(type="note", content="seed")])
    peer.patch_entity_metadata(ns, seed.id, {"admin": "changed"})
    with pytest.raises(ConcurrentEntityUpdate):
        client.backend.commit_prepared(ns, [prepared], checkpoint=("batch", {}))
    assert client.backend.scan_entities(ns)[0].content == "seed"
    assert client.backend.get_processing_checkpoint(ns, "batch") is None


def test_hook_patches_are_proposals_and_commit_without_callback_locks(storage, monkeypatch):
    import asyncio
    from altk_evolve.hooks.backend import HookBackend
    from altk_evolve.hooks.manager import _run_sync

    client, peer, ns = storage
    entity_id = client.backend.scan_entities(ns)[0].id

    def dispatch(backend, namespace, entities):
        capability = HookBackend(backend)

        async def hook():
            capability.update_entity_metadata(ns, entity_id, {"learned": True})
            assert capability.search_entities(ns)[0].metadata == {"learned": True}
            assert peer.backend.scan_entities(ns)[0].metadata == {}
            return entities

        return _run_sync(hook())

    monkeypatch.setattr("altk_evolve.backend.base.dispatch_memory_pre_write", dispatch)

    async def run():
        prepared = client.backend.prepare_updates(ns, [Entity(type="note", content="new")], False)
        assert peer.backend.scan_entities(ns)[0].metadata == {}
        client.backend.commit_prepared(ns, [prepared])

    asyncio.run(run())
    assert peer.backend.scan_entities(ns, filters={"id": entity_id})[0].metadata == {"learned": True}


def test_checkpoint_failure_rolls_back_outputs_and_metadata(storage, monkeypatch):
    client, peer, ns = storage
    prepared = client.backend.prepare_updates(ns, [Entity(type="note", content="new")], False)
    original = client.backend._save_processing_checkpoint
    monkeypatch.setattr(client.backend, "_save_processing_checkpoint", lambda *args: (_ for _ in ()).throw(OSError("checkpoint failed")))
    with pytest.raises(OSError, match="checkpoint failed"):
        client.backend.commit_prepared(ns, [prepared], checkpoint=("batch", {}))
    assert [e.content for e in peer.backend.scan_entities(ns)] == ["seed"]
    monkeypatch.setattr(client.backend, "_save_processing_checkpoint", original)
    assert client.backend.commit_prepared(ns, [prepared], checkpoint=("batch", {})) is not None
    assert client.backend.commit_prepared(ns, [prepared], checkpoint=("batch", {})) is None
    assert len(peer.backend.scan_entities(ns)) == 2


def test_incremental_batches_capture_config_and_keep_context_separate(storage):
    from threading import Event
    from altk_evolve.processing import ProcessingManager

    client, peer, ns = storage
    client._processing = ProcessingManager()
    started, release = Event(), Event()
    seen = []

    def inspect(trajectory, config):
        seen.append((trajectory.batch.batch_id, trajectory.messages, trajectory.context_messages, config.label))
        if trajectory.batch.batch_id == "1-120":
            started.set()
            assert release.wait(5)

    definition = install_processor(client, inspect)
    client.processing.put("mode", definition, expected_revision=0)
    first = {"messages": [{"role": "assistant", "content": "first range"}], "batch": source_batch("1-120")}
    second = {
        "messages": [{"role": "assistant", "content": "new range"}],
        "context_messages": first["messages"],
        "batch": source_batch("121-140"),
    }
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(client.process_trajectory, first, namespace_id=ns, processing_profile="mode")
        try:
            assert started.wait(5)
            peer.update_entities(ns, [Entity(type="note", content="unrelated")], False)
            definition["processors"][0]["config"] = {"label": "second"}
            client.processing.put("mode", definition, expected_revision=1)
        finally:
            release.set()
        assert future.result(timeout=5).completed_processors == ["learner"]
    assert client.process_trajectory(second, namespace_id=ns, processing_profile="mode").completed_processors == ["learner"]
    assert client.process_trajectory(first, namespace_id=ns, processing_profile="mode").skipped_processors == ["learner"]
    assert client.process_trajectory(second, namespace_id=ns, processing_profile="mode").skipped_processors == ["learner"]
    assert seen == [("1-120", first["messages"], [], "first"), ("121-140", second["messages"], first["messages"], "second")]
    assert {e.content for e in peer.backend.scan_entities(ns)} == {"seed", "unrelated", "first", "second"}


def test_duplicate_deliveries_commit_only_one_contribution(storage):
    from threading import Barrier
    from altk_evolve.processing import ProcessingManager

    client, peer, ns = storage
    client._processing = ProcessingManager()
    gate = Barrier(2)
    definition = install_processor(client, lambda *_: gate.wait(timeout=5))
    plan = client.processing.validate(definition)

    def process(_):
        return client.process_trajectory({"messages": [], "batch": source_batch()}, namespace_id=ns, plan=plan)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(process, range(2)))
    assert sum(bool(result.completed_processors) for result in results) == 1
    assert sum(bool(result.skipped_processors) for result in results) == 1
    assert len(peer.backend.scan_entities(ns)) == 2


def test_progress_is_per_processor_and_failed_processor_remains_eligible(storage, monkeypatch):
    from altk_evolve.processing import ProcessingManager

    client, peer, ns = storage
    client._processing = ProcessingManager()
    runs = []
    definition = install_processor(client, lambda *args: runs.append(1))
    definition["processors"].append({**definition["processors"][0], "id": "second"})
    plan = client.processing.validate(definition)
    original = client.backend._save_processing_checkpoint

    def fail_second(namespace, key, record):
        if record["processor_id"] == "second":
            raise OSError("second failed")
        original(namespace, key, record)

    monkeypatch.setattr(client.backend, "_save_processing_checkpoint", fail_second)
    trajectory = {"messages": [], "batch": source_batch()}
    with pytest.raises(OSError):
        client.process_trajectory(trajectory, namespace_id=ns, plan=plan)
    assert len(peer.backend.scan_entities(ns)) == 2
    monkeypatch.setattr(client.backend, "_save_processing_checkpoint", original)
    result = client.process_trajectory(trajectory, namespace_id=ns, plan=plan)
    assert result.skipped_processors == ["learner"]
    assert result.completed_processors == ["second"]
    assert len(runs) == 3
    assert len(peer.backend.scan_entities(ns)) == 3


def test_commit_does_not_run_hooks_or_compute_embeddings(storage, monkeypatch):
    client, peer, ns = storage
    prepared = client.backend.prepare_updates(ns, [Entity(type="note", content="prepared")], False)

    def unexpected(*args, **kwargs):
        raise AssertionError("Commit must perform storage operations only")

    monkeypatch.setattr("altk_evolve.backend.base.dispatch_memory_pre_write", unexpected)
    monkeypatch.setattr("altk_evolve.backend.base.dispatch_memory_pre_metadata_patch", unexpected)
    if client.config.backend == "postgres":
        monkeypatch.setattr(client.backend.embedding_model, "encode", unexpected)
    client.backend.commit_prepared(ns, [prepared], checkpoint=("storage-only", {}))
    assert {e.content for e in peer.backend.scan_entities(ns)} == {"seed", "prepared"}


def test_metadata_proposal_for_deleted_entity_does_not_abort_commit(storage, monkeypatch):
    from altk_evolve.backend.writes import MetadataPatch
    from altk_evolve.schema.conflict_resolution import EntityUpdate

    client, peer, ns = storage
    seed = client.backend.scan_entities(ns)[0]
    monkeypatch.setattr(
        "altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts",
        lambda *a, **kw: [EntityUpdate(id=seed.id, type="note", content=seed.content, event="DELETE")],
    )
    prepared = client.backend.prepare_updates(ns, [Entity(type="note", content="seed")])
    prepared.patches.append(MetadataPatch(ns, seed.id, {"accessed": True}))
    client.backend.commit_prepared(ns, [prepared], checkpoint=("delete-batch", {}))
    assert peer.backend.scan_entities(ns) == []
    assert peer.backend.get_processing_checkpoint(ns, "delete-batch") == {}


def test_queued_legal_hold_is_enforced_before_conflict_delete(storage, monkeypatch):
    from altk_evolve.hooks.backend import HookBackend
    from altk_evolve.hooks.manager import MemoryPolicyViolation
    from altk_evolve.schema.conflict_resolution import EntityUpdate

    client, peer, ns = storage
    seed = client.backend.scan_entities(ns)[0]

    def before_write(backend, namespace, entities):
        HookBackend(backend).update_entity_metadata(namespace, seed.id, {"legal_hold": True})
        return entities

    def before_delete(backend, namespace, entity_id, *, metadata):
        assert metadata["legal_hold"] is True
        raise MemoryPolicyViolation(plugin_name="hold", hook_type="memory_pre_delete", code="hold", reason="held")

    def resolve(old, new, **kwargs):
        assert old[0].metadata["legal_hold"] is True
        return [EntityUpdate(id=seed.id, type="note", content=seed.content, event="DELETE")]

    monkeypatch.setattr("altk_evolve.backend.base.dispatch_memory_pre_write", before_write)
    monkeypatch.setattr("altk_evolve.backend.base.dispatch_memory_pre_delete", before_delete)
    monkeypatch.setattr("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", resolve)
    client.update_entities(ns, [Entity(type="note", content="seed")])
    assert peer.backend.scan_entities(ns)[0].metadata["legal_hold"] is True


def test_namespace_delete_in_transaction_fails_before_reacquiring_lock(storage):
    from altk_evolve.schema.exceptions import EvolveException
    from threading import Thread

    client, _, ns = storage
    errors = []

    def delete():
        try:
            with client.backend.transaction(ns):
                client.backend.delete_namespace(ns)
        except EvolveException as exc:
            errors.append(str(exc))

    thread = Thread(target=delete, daemon=True)
    thread.start()
    thread.join(timeout=3)
    assert not thread.is_alive(), "nested delete deadlocked"
    assert len(errors) == 1 and "transaction" in errors[0]


def test_alias_checkpoint_linking_is_atomic_and_preserves_original_provenance(storage, monkeypatch):
    client, peer, ns = storage
    client.backend.commit_prepared(ns, [], checkpoint=("outer", {"revision": 1}))
    save = client.backend._save_processing_checkpoint

    def fail_last(namespace, key, value):
        if key == "last":
            raise RuntimeError("checkpoint failure")
        save(namespace, key, value)

    monkeypatch.setattr(client.backend, "_save_processing_checkpoint", fail_last)
    with pytest.raises(RuntimeError, match="checkpoint failure"):
        client.backend.commit_prepared(ns, [], checkpoint=("inner", {}), checkpoint_aliases=("outer", "last"))
    assert peer.backend.get_processing_checkpoint(ns, "inner") is None
    monkeypatch.setattr(client.backend, "_save_processing_checkpoint", save)
    assert client.backend.commit_prepared(ns, [], checkpoint=("inner", {}), checkpoint_aliases=("outer",)) is None
    assert peer.backend.get_processing_checkpoint(ns, "inner") == {"revision": 1}
