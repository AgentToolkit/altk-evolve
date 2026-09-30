"""Real PostgreSQL atomic output/marker commits, using deterministic embeddings."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import numpy as np
import pytest
from pydantic import BaseModel

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.postgres import PostgresDBSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.processing import ProcessorResult, ProfileConflict, ProfileNotFound
from altk_evolve.schema.core import Entity
from altk_evolve.sync.phoenix_sync import PhoenixSync

pytestmark = pytest.mark.e2e


class NoteConfig(BaseModel):
    pass


class NoteProcessor:
    id = "tests.note"
    api_version = 1
    version = "1"
    config_model = NoteConfig

    @classmethod
    def from_config(cls, config):
        return cls()

    def process(self, trajectory, *, context):
        return ProcessorResult(entities=[Entity(type="note", content="output")])


class Embeddings:
    def get_sentence_embedding_dimension(self):
        return 3

    def encode(self, content):
        return np.array([1.0, 0.0, 0.0])


@pytest.fixture
def sync(tmp_path, monkeypatch):
    from psycopg.conninfo import conninfo_to_dict

    dsn = os.getenv("EVOLVE_TEST_SCHEDULE_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set EVOLVE_TEST_SCHEDULE_POSTGRES_DSN to a disposable pgvector database")
    options = conninfo_to_dict(dsn)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    monkeypatch.setenv("EVOLVE_SQLITE_PATH", str(tmp_path / "namespaces.db"))
    monkeypatch.setattr("altk_evolve.backend.postgres.SentenceTransformer", lambda _: Embeddings())
    settings = PostgresDBSettings(**{key: value for key, value in options.items() if key in PostgresDBSettings.model_fields})
    client = EvolveClient(EvolveConfig(backend="postgres", settings=settings))
    client.processing.registry.register(NoteProcessor)
    namespace = "processing_" + uuid.uuid4().hex
    client.processing.put(namespace, {"processors": [{"id": "note", "plugin": "tests.note"}]}, expected_revision=0)
    client.create_namespace(namespace)
    with patch("altk_evolve.sync.phoenix_sync.EvolveClient", return_value=client):
        syncer = PhoenixSync(namespace_id=namespace, processing_profile=namespace)
    try:
        yield syncer
    finally:
        client.backend.conn.execute("DELETE FROM processing_profiles WHERE id=%s", (namespace,))
        client.delete_namespace(namespace)
        client.backend.close()


def trajectory():
    return dict(messages=[dict(role="user", content="hello")], trace_id="trace", span_id="span", model="unknown", timestamp=0)


def test_postgres_marker_failure_rolls_back_and_retry_commits_once(sync, monkeypatch):
    client = sync.client
    original = client.backend._save_processing_checkpoint

    def fail_marker(*args):
        raise OSError("marker failed")

    monkeypatch.setattr(client.backend, "_save_processing_checkpoint", fail_marker)
    with pytest.raises(OSError, match="marker failed"):
        sync._process_trajectory(trajectory())
    assert client.get_all_entities(sync.namespace_id) == []
    monkeypatch.setattr(client.backend, "_save_processing_checkpoint", original)
    sync._process_trajectory(trajectory())
    sync._process_trajectory(trajectory())
    assert sorted(e.type for e in client.get_all_entities(sync.namespace_id)) == ["note"]


def test_postgres_concurrent_duplicate_deliveries_commit_once(sync):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: sync._process_trajectory(trajectory()), range(2)))
    assert sorted(e.type for e in sync.client.get_all_entities(sync.namespace_id)) == ["note"]


def test_profiles_share_postgres_database_and_survive_new_client(sync):
    import sqlite3

    client = sync.client
    name = sync.processing_profile
    assert client.backend.conn.execute("SELECT revision FROM processing_profiles WHERE id=%s", (name,)).fetchone() == (1,)
    with sqlite3.connect(os.environ["EVOLVE_SQLITE_PATH"]) as metadata:
        assert metadata.execute("SELECT name FROM sqlite_master WHERE name='processing_profiles'").fetchone() is None
    peer = EvolveClient(client.config)
    try:
        assert peer.processing.get(name) == client.processing.get(name)
        peer.processing.put(name, {"processors": []}, expected_revision=1)
        assert client.processing.get(name)["revision"] == 2
        assert client.processing.get(name, revision=1)["manifest"]["processors"][0]["plugin"] == "tests.note"
        with pytest.raises(ProfileNotFound):
            peer.processing.get(name, revision=99)
    finally:
        peer.backend.close()


def test_postgres_profile_updates_reject_stale_writers_and_roll_back(sync):
    from threading import Barrier

    client = sync.client
    peer = EvolveClient(client.config)
    name = sync.processing_profile
    gate = Barrier(2)

    def publish(manager):
        gate.wait(timeout=10)
        try:
            return manager.put(name, {"processors": []}, expected_revision=1)["revision"]
        except ProfileConflict:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, [client.processing, peer.processing]))
        assert set(results) == {2, "conflict"}
        repository = peer.processing.repository
        with pytest.raises(TypeError):
            repository.put(name, {"invalid": object()}, expected_revision=2)
        assert client.processing.get(name)["revision"] == 2
        peer.processing.put(name, {"processors": []}, expected_revision=2)
        assert client.processing.get(name)["revision"] == 3
    finally:
        peer.backend.close()


def test_postgres_checkpoints_are_independent_of_entity_hooks(sync, monkeypatch):
    client = sync.client
    monkeypatch.setattr("altk_evolve.backend.base.dispatch_memory_post_read", lambda *args, **kw: [])
    assert sync._process_trajectory(trajectory()) == 0
    assert sync._process_trajectory(trajectory()) is None
    assert len(client.backend.scan_entities(sync.namespace_id)) == 1
    assert client.backend.conn.execute(
        "SELECT count(*) FROM processing_checkpoints WHERE namespace_id=%s", (sync.namespace_id,)
    ).fetchone() == (1,)


def test_processing_validates_catalog_without_counting_entities(sync, monkeypatch):
    client = sync.client
    monkeypatch.setattr(client.backend, "get_namespace_details", lambda *_: pytest.fail("processing must not count entities"))
    result = client.process_trajectory({"messages": []}, namespace_id=sync.namespace_id, processing_profile=sync.namespace_id)
    assert result.completed_processors == ["note"]


def test_postgres_search_indexes_are_explicit_and_idempotent(sync):
    client = sync.client
    backend = client.backend
    backend.create_search_indexes(sync.namespace_id)
    backend.create_search_indexes(sync.namespace_id, approximate=True)
    backend.create_search_indexes(sync.namespace_id, approximate=True)
    rows = backend.conn.execute("SELECT indexdef FROM pg_indexes WHERE tablename=%s", (backend._table_name(sync.namespace_id),)).fetchall()
    definitions = "\n".join(row[0] for row in rows)
    assert "USING gin (metadata jsonb_path_ops)" in definitions
    assert "USING btree (type)" in definitions
    assert "USING hnsw (embedding vector_cosine_ops)" in definitions
    client.update_entities(sync.namespace_id, [Entity(type="note", content="needle", metadata={"rare": True})], False)
    assert (
        client.backend.search_entities(sync.namespace_id, query="needle", filters={"metadata.rare": True}, limit=1)[0].content == "needle"
    )


def test_index_maintenance_repairs_interrupted_build(sync):
    backend = sync.client.backend
    table = backend._table_name(sync.namespace_id)
    name = backend.conn.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename=%s AND indexdef LIKE '%%USING gin%%'", (table,)
    ).fetchone()[0]
    # Simulate the catalog state left by an interrupted concurrent index build.
    backend.conn.execute("UPDATE pg_index SET indisvalid=false WHERE indexrelid=to_regclass(%s)", (name,))
    backend.create_search_indexes(sync.namespace_id)
    assert backend.conn.execute("SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass(%s)", (name,)).fetchone() == (True,)


def test_processing_rejects_namespace_missing_from_catalog(sync):
    import sqlite3
    from altk_evolve.schema.exceptions import NamespaceNotFoundException

    with sqlite3.connect(os.environ["EVOLVE_SQLITE_PATH"]) as connection:
        connection.execute("DELETE FROM namespaces WHERE id=?", (sync.namespace_id,))
    with pytest.raises(NamespaceNotFoundException):
        sync.client.process_trajectory({"messages": []}, namespace_id=sync.namespace_id, processing_profile=sync.namespace_id)


@pytest.mark.parametrize("event", ["NONE", "UPDATE"])
def test_postgres_provenance_and_checkpoint_are_atomic(sync, monkeypatch, event):
    from altk_evolve.schema.conflict_resolution import EntityUpdate

    backend, namespace = sync.client.backend, sync.namespace_id

    def source(thread):
        return Entity(type="fact", content="preference", metadata={"user_id": "alice", "thread_id": thread})

    backend.update_entities(namespace, [source("first")], False)
    before = backend.scan_entities(namespace)

    def reconcile(old, new, **kwargs):
        assert not backend.in_transaction
        return [
            EntityUpdate(
                id=old[0].id, type="fact", content="changed" if event == "UPDATE" else old[0].content, event=event, incoming_ids=[new[0].id]
            )
        ]

    monkeypatch.setattr("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", reconcile)
    prepared = backend.prepare_updates(namespace, [source("second")])
    assert backend.scan_entities(namespace) == before
    save_checkpoint = backend._save_processing_checkpoint

    def fail(*args):
        raise OSError("checkpoint failed")

    monkeypatch.setattr(backend, "_save_processing_checkpoint", fail)
    with pytest.raises(OSError, match="checkpoint failed"):
        backend.commit_prepared(namespace, [prepared], checkpoint=("sources", {}))
    assert backend.scan_entities(namespace) == before
    assert backend.get_processing_checkpoint(namespace, "sources") is None
    monkeypatch.setattr(backend, "_save_processing_checkpoint", save_checkpoint)
    backend.commit_prepared(namespace, [prepared], checkpoint=("sources", {}))
    stored = backend.scan_entities(namespace)[0]
    assert {s["conversation_id"] for s in stored.metadata["sources"]} == {"first", "second"}
    assert stored.metadata["memory_revision"] == (2 if event == "UPDATE" else 1)
    assert backend.commit_prepared(namespace, [prepared], checkpoint=("sources", {})) is None


def test_postgres_concurrent_reaffirmations_preserve_sources(sync, monkeypatch):
    from threading import Barrier, Lock
    from altk_evolve.schema.conflict_resolution import EntityUpdate

    client, namespace = sync.client, sync.namespace_id

    def source(thread):
        return Entity(type="fact", content="preference", metadata={"user_id": "alice", "thread_id": thread})

    client.backend.update_entities(namespace, [source("original")], False)
    peer = EvolveClient(client.config)
    barrier, lock = Barrier(2), Lock()
    calls = 0

    def reconcile(old, new, **kwargs):
        nonlocal calls
        assert not client.backend.in_transaction and not peer.backend.in_transaction
        with lock:
            calls += 1
            wait = calls <= 2
        if wait:
            barrier.wait(timeout=10)
        return [EntityUpdate(id=old[0].id, type="fact", content=old[0].content, event="NONE", incoming_ids=[new[0].id])]

    monkeypatch.setattr("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", reconcile)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(backend.update_entities, namespace, [source(thread)])
                for backend, thread in [(client.backend, "one"), (peer.backend, "two")]
            ]
            for future in futures:
                future.result(timeout=15)
        stored = client.backend.scan_entities(namespace)[0]
        assert {s["conversation_id"] for s in stored.metadata["sources"]} == {"original", "one", "two"}
        assert calls == 3
    finally:
        peer.backend.close()
