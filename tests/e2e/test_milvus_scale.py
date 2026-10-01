"""Real Milvus Lite regressions for bounded reads and concurrent metadata merges."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import numpy as np
import pytest

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.milvus import MilvusDBSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import Entity
from altk_evolve.schema.exceptions import EvolveException

pytestmark = pytest.mark.e2e


@pytest.fixture
def memory(tmp_path, monkeypatch):
    pytest.importorskip("milvus_lite")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    monkeypatch.setenv("EVOLVE_SQLITE_PATH", str(tmp_path / "metadata.db"))

    class Embeddings:
        def encode(self, content):
            vector = np.zeros(384, dtype=np.float32)
            vector[1 if "rare" in content else 0] = 1
            return vector

    monkeypatch.setattr("altk_evolve.backend.milvus.SentenceTransformer", lambda _: Embeddings())
    config = EvolveConfig(backend="milvus", settings=MilvusDBSettings(uri=str(tmp_path / "milvus.db")))
    clients = [EvolveClient(config) for _ in range(16)]
    client = clients[0]
    client.create_namespace("memory")
    client.backend.milvus.insert(
        "memory",
        [
            {"type": "note", "content": f"ordinary {i}", "created_at": 1, "embedding": Embeddings().encode("ordinary"), "metadata": {}}
            for i in range(1200)
        ],
    )
    client.backend._post_update("memory")
    entity_id = client.update_entities("memory", [Entity(type="note", content="rare held target", metadata={"legal_hold": True})], False)[
        0
    ].id
    try:
        yield clients, entity_id
    finally:
        client.delete_namespace("memory")
        for c in clients:
            c.backend.close()


def test_filtered_retrieval_before_limit_and_exact_id(memory):
    clients, entity_id = memory
    backend = clients[0].backend
    assert backend.scan_entities("memory", filters={"id": entity_id}, limit=1)[0].id == entity_id
    for query in (None, "ordinary"):
        found = backend.search_entities("memory", query=query, filters={"metadata.legal_hold": True, "type": "note"}, limit=1)
        assert [e.id for e in found] == [entity_id]


def test_delete_hook_receives_metadata_beyond_candidate_window(memory, monkeypatch):
    clients, entity_id = memory

    def policy(backend, namespace, entity_id, metadata=None):
        if metadata and metadata.get("legal_hold"):
            raise EvolveException("held")

    monkeypatch.setattr("altk_evolve.backend.base.hooks_active", lambda _: True)
    monkeypatch.setattr("altk_evolve.backend.base.dispatch_memory_pre_delete", policy)
    with pytest.raises(EvolveException, match="held"):
        clients[0].delete_entity_by_id("memory", entity_id)
    assert clients[0].backend.milvus.query("memory", filter=f"id == {entity_id}", output_fields=["id"], limit=1)


def test_concurrent_clients_preserve_disjoint_metadata(memory):
    clients, entity_id = memory
    gate = Barrier(len(clients))

    def patch(user):
        gate.wait(timeout=10)
        clients[user].patch_entity_metadata("memory", entity_id, {f"user_{user}": True})

    with ThreadPoolExecutor(len(clients)) as pool:
        list(pool.map(patch, range(len(clients))))
    rows = clients[0].backend.milvus.query(
        "memory", filter=f"id == {entity_id}", output_fields=["metadata"], limit=1, consistency_level="Strong"
    )
    assert rows[0]["metadata"] == {"legal_hold": True, **{f"user_{i}": True for i in range(16)}}


@pytest.mark.parametrize("change", ["sources", "legal_hold"])
def test_stale_provenance_cannot_overwrite_concurrent_metadata(memory, monkeypatch, change):
    from altk_evolve.schema.conflict_resolution import EntityUpdate

    clients, entity_id = memory
    backend = clients[0].backend
    backend.update_entity_metadata("memory", entity_id, {"legal_hold": False})

    def reconcile(old, new):
        target = next(entity for entity in old if entity.id == entity_id)
        return [EntityUpdate(id=target.id, type="note", content=target.content, event="NONE", incoming_ids=[new[0].id])]

    monkeypatch.setattr("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", reconcile)
    incoming = Entity(type="note", content="rare held target", metadata={"thread_id": "incoming"})
    prepared = backend.prepare_updates("memory", [incoming])
    concurrent = {"sources": [{"conversation_id": "concurrent", "status": "supporting"}]} if change == "sources" else {"legal_hold": True}
    clients[1].patch_entity_metadata("memory", entity_id, concurrent)
    with pytest.raises(EvolveException, match="changed during provenance"):
        backend.commit_prepared("memory", [prepared])
    stored = backend.search_entities("memory", filters={"id": entity_id})[0]
    assert stored.metadata[change] == concurrent[change]
    # Fresh preparation succeeds without discarding the concurrent evidence/hold.
    backend.update_entities("memory", [incoming])
    stored = backend.search_entities("memory", filters={"id": entity_id})[0]
    if change == "sources":
        assert {s["conversation_id"] for s in stored.metadata["sources"]} == {"concurrent", "incoming"}
    else:
        assert stored.metadata["legal_hold"] is True
