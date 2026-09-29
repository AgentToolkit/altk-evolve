"""The same concurrency contract runs against JSON storage and real PostgreSQL."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from altk_evolve.backend.base import ConcurrentNamespaceUpdate
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


def test_concurrent_writer_completes_during_preparation_and_invalidates_snapshot(storage):
    client, peer, ns = storage
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(ConcurrentNamespaceUpdate):
            with client.backend.transaction(ns):
                client.update_entities(ns, [Entity(type="note", content="uncommitted")], enable_conflict_resolution=False)
                observed = pool.submit(peer.backend.scan_entities, ns).result(timeout=5)
                assert [entity.content for entity in observed] == ["seed"]
                pool.submit(peer.update_entities, ns, [Entity(type="note", content="concurrent")], False).result(timeout=5)
                # Reads inside preparation remain isolated, including own pending writes.
                assert [e.content for e in client.backend.scan_entities(ns)] == ["seed", "uncommitted"]
    assert sorted(e.content for e in client.backend.scan_entities(ns)) == ["concurrent", "seed"]


def test_metadata_change_alone_invalidates_preparation(storage):
    client, peer, ns = storage
    entity = client.backend.scan_entities(ns)[0]
    with pytest.raises(ConcurrentNamespaceUpdate):
        with client.backend.transaction(ns):
            client.update_entities(ns, [Entity(type="note", content="stale")], enable_conflict_resolution=False)
            peer.patch_entity_metadata(ns, entity.id, {"admin": "changed"})
    assert len(client.backend.scan_entities(ns)) == 1
    assert client.backend.scan_entities(ns)[0].metadata == {"admin": "changed"}


def test_hook_bridge_patches_private_snapshot_without_lock_ownership(storage):
    import asyncio
    from altk_evolve.hooks.backend import HookBackend
    from altk_evolve.hooks.manager import _run_sync

    client, peer, ns = storage
    entity_id = client.backend.scan_entities(ns)[0].id
    hook_backend = HookBackend(client.backend)

    async def hook():
        hook_backend.update_entity_metadata(ns, entity_id, {"staged": True})
        assert hook_backend.search_entities(ns)[0].metadata == {"staged": True}

    async def run():
        with client.backend.transaction(ns):
            _run_sync(hook())
            assert peer.backend.scan_entities(ns)[0].metadata == {}
            assert client.backend.scan_entities(ns)[0].metadata == {"staged": True}

    asyncio.run(run())
    assert peer.backend.scan_entities(ns)[0].metadata == {"staged": True}
    for name in ("conn", "transaction", "create_namespace", "delete_namespace", "update_entities"):
        assert not hasattr(hook_backend, name)


@pytest.mark.parametrize("exhaust", [False, True])
def test_retry_reuses_generation_and_refreshes_conflict_reads(storage, monkeypatch, exhaust):
    from pydantic import BaseModel
    from altk_evolve.processing import ProcessorResult
    from altk_evolve.schema.conflict_resolution import EntityUpdate
    from altk_evolve.sync.phoenix_sync import PhoenixSync

    client, peer, ns = storage
    runs = []
    reads = []

    class Config(BaseModel):
        pass

    class Processor:
        id = "test.retry"
        api_version = 1
        version = "1"
        config_model = Config

        @classmethod
        def from_config(cls, config):
            return cls()

        def process(self, trajectory, *, context):
            runs.append(context.operation_id)
            return ProcessorResult(entities=[Entity(type="note", content="seed")], enable_conflict_resolution=True)

    def resolve(old, new, **kwargs):
        reads.append({e.id: e.metadata for e in old})
        if len(reads) == 1 or exhaust:
            with ThreadPoolExecutor(1) as pool:
                pool.submit(peer.patch_entity_metadata, ns, old[0].id, {"concurrent": len(reads)}).result(timeout=5)
        return [EntityUpdate(id="temporary", type="note", content="output", event="ADD")]

    client.processing.registry.register(Processor)
    client.processing.put(ns, {"processors": [{"id": "retry", "plugin": Processor.id}]}, expected_revision=0)
    monkeypatch.setattr("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", resolve)
    with patch("altk_evolve.sync.phoenix_sync.EvolveClient", return_value=client):
        sync = PhoenixSync(namespace_id=ns, processing_profile=ns)
    trace = dict(messages=[dict(role="user", content="hello")], trace_id="trace", span_id="span", model="unknown", timestamp=0)
    try:
        if exhaust:
            with pytest.raises(ConcurrentNamespaceUpdate):
                sync._process_trajectory(trace)
            assert len(runs) == 1
            assert len(reads) == 3
            assert [e.content for e in client.backend.scan_entities(ns)] == ["seed"]
            assert client.backend.scan_entities(ns)[0].metadata == {"concurrent": 3}
            return
        sync._process_trajectory(trace)
        assert len(runs) == 1
        assert len(reads) == 2
        assert list(reads[0].values()) == [{}]
        assert list(reads[1].values()) == [{"concurrent": 1}]
        stored = client.backend.scan_entities(ns)
        assert sorted(e.content for e in stored if e.type == "note") == ["output", "seed"]
        assert sum(e.type == "trajectory" for e in stored) == 1
        assert sync._process_trajectory(trace) is None
        assert len(runs) == 1
    finally:
        if client.config.backend == "postgres":
            client.backend.conn.execute("DELETE FROM processing_profiles WHERE id=%s", (ns,))


def test_failed_preparation_discards_deletes_and_hook_metadata(storage):
    client, peer, ns = storage
    seed = client.backend.scan_entities(ns)[0]
    with pytest.raises(RuntimeError, match="abort"):
        with client.backend.transaction(ns):
            client.patch_entity_metadata(ns, seed.id, {"prepared": True})
            client.delete_entity_by_id(ns, seed.id)
            client.update_entities(ns, [Entity(type="trajectory", content="marker")], enable_conflict_resolution=False)
            assert [e.type for e in client.backend.scan_entities(ns)] == ["trajectory"]
            raise RuntimeError("abort")
    assert peer.backend.scan_entities(ns) == [seed]


def test_parallel_working_copies_on_same_backend_do_not_mix(storage):
    from threading import Barrier

    client, peer, ns = storage
    gate = Barrier(2)

    def write(content):
        try:
            with client.backend.transaction(ns):
                client.update_entities(ns, [Entity(type="note", content=content)], enable_conflict_resolution=False)
                assert {e.content for e in client.backend.scan_entities(ns)} == {"seed", content}
                gate.wait(timeout=5)
            return content
        except ConcurrentNamespaceUpdate:
            return None

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(write, ["first", "second"]))
    winners = [value for value in outcomes if value is not None]
    assert len(winners) == 1
    assert {e.content for e in peer.backend.scan_entities(ns)} == {"seed", winners[0]}
