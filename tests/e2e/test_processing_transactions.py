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
    original = client.update_entities

    def fail_marker(namespace, entities, **kwargs):
        if entities[0].type == "trajectory":
            raise OSError("marker failed")
        return original(namespace, entities, **kwargs)

    monkeypatch.setattr(client, "update_entities", fail_marker)
    with pytest.raises(OSError, match="marker failed"):
        sync._process_trajectory(trajectory())
    assert client.get_all_entities(sync.namespace_id) == []
    monkeypatch.setattr(client, "update_entities", original)
    sync._process_trajectory(trajectory())
    sync._process_trajectory(trajectory())
    assert sorted(e.type for e in client.get_all_entities(sync.namespace_id)) == ["note", "trajectory"]


def test_postgres_concurrent_duplicate_deliveries_commit_once(sync):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: sync._process_trajectory(trajectory()), range(2)))
    assert sorted(e.type for e in sync.client.get_all_entities(sync.namespace_id)) == ["note", "trajectory"]


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
