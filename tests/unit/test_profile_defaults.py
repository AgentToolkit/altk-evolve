"""Persisted service defaults follow configuration without losing operator edits."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from altk_evolve.config import llm
from altk_evolve.processing import ProcessingManager, SQLiteProfileRepository

pytestmark = pytest.mark.unit
DEFINITION = {
    "processors": [{"id": "guidelines", "plugin": "evolve.guidelines", "config": {"guidelines_mode": "all", "consistency_method": "fast"}}]
}


def settings(monkeypatch, model, provider):
    monkeypatch.setenv("EVOLVE_MODEL_NAME", model)
    monkeypatch.setenv("EVOLVE_GUIDELINES_MODEL", model)
    monkeypatch.setenv("EVOLVE_CONFLICT_RESOLUTION_MODEL", model)
    monkeypatch.setenv("EVOLVE_CUSTOM_LLM_PROVIDER", provider)
    monkeypatch.setattr(llm, "llm_settings", llm.LLMSettings(_env_file=None))


def manager(path):
    return ProcessingManager(repository=SQLiteProfileRepository(path))


def test_refresh_reopens_storage_and_preserves_operator_override(tmp_path, monkeypatch):
    path = tmp_path / "profiles.db"
    settings(monkeypatch, "old-model", "openai")
    first = manager(path).ensure("service", DEFINITION)
    assert first["revision"] == 1
    assert manager(path).ensure("service", DEFINITION)["revision"] == 1
    settings(monkeypatch, "new-model", "ollama")
    refreshed = manager(path).ensure("service", DEFINITION)
    assert refreshed["revision"] == 2
    assert refreshed["manifest"]["processors"][0]["config"]["guidelines_model"] == "new-model"
    assert refreshed["manifest"]["conflict_resolution"]["custom_llm_provider"] == "ollama"
    config = dict(refreshed["manifest"]["processors"][0]["config"], guidelines_model="operator-model", guidelines_mode="standard")
    manager(path).put(
        "service", {"processors": [{"id": "guidelines", "plugin": "evolve.guidelines", "config": config}]}, expected_revision=2
    )
    settings(monkeypatch, "third-model", "openai")
    final = manager(path).ensure("service", DEFINITION)
    assert final["manifest"]["processors"][0]["config"]["guidelines_model"] == "operator-model"
    assert final["manifest"]["processors"][0]["config"]["guidelines_mode"] == "standard"
    assert final["manifest"]["processors"][0]["config"]["custom_llm_provider"] == "openai"
    assert final["manifest"]["conflict_resolution"]["conflict_resolution_model"] == "third-model"
    assert manager(path).resolve("service").manifest() == final["manifest"]
    assert manager(path).resolve("service", revision=1).manifest() == first["manifest"]


def test_adopts_legacy_profile_and_concurrent_replicas_converge(tmp_path, monkeypatch):
    path = tmp_path / "profiles.db"
    settings(monkeypatch, "old-model", "openai")
    manager(path).put("service", DEFINITION, expected_revision=0)
    settings(monkeypatch, "new-model", "ollama")
    replicas = [manager(path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda m: m.ensure("service", DEFINITION), replicas))
    assert {result["revision"] for result in results} == {2}
    assert results[0]["manifest"]["processors"][0]["config"]["guidelines_model"] == "new-model"


def test_refresh_retries_after_concurrent_operator_edit(tmp_path, monkeypatch):
    path = tmp_path / "profiles.db"
    settings(monkeypatch, "old-model", "openai")
    first = manager(path).ensure("service", DEFINITION)
    settings(monkeypatch, "new-model", "ollama")
    refreshing = manager(path)
    original_put = refreshing.repository.put
    raced = False

    def put(name, value, *, expected_revision):
        nonlocal raced
        if not raced:
            raced = True
            config = dict(first["manifest"]["processors"][0]["config"], guidelines_model="operator-model")
            manager(path).put(
                name,
                {"processors": [{"id": "guidelines", "plugin": "evolve.guidelines", "config": config}]},
                expected_revision=expected_revision,
            )
        return original_put(name, value, expected_revision=expected_revision)

    monkeypatch.setattr(refreshing.repository, "put", put)
    result = refreshing.ensure("service", DEFINITION)
    assert result["revision"] == 3
    assert result["manifest"]["processors"][0]["config"]["guidelines_model"] == "operator-model"
    assert result["manifest"]["processors"][0]["config"]["custom_llm_provider"] == "ollama"
