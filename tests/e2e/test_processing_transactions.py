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
from altk_evolve.processing import ProcessingManager, ProcessorRegistry, ProcessorResult
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
    registry = ProcessorRegistry()
    registry.register(NoteProcessor)
    processing = ProcessingManager(registry=registry)
    processing.put("review", {"processors": [{"id": "note", "plugin": "tests.note"}]}, expected_revision=0)
    client = EvolveClient(EvolveConfig(backend="postgres", settings=settings), processing=processing)
    namespace = "processing_" + uuid.uuid4().hex
    client.create_namespace(namespace)
    with patch("altk_evolve.sync.phoenix_sync.EvolveClient", return_value=client):
        syncer = PhoenixSync(namespace_id=namespace, processing_profile="review")
    try:
        yield syncer
    finally:
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
