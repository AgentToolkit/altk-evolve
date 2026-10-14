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


def test_operator_replacement_processor_keeps_its_own_config():
    from altk_evolve.processing.manager import _refresh_inherited

    before = {"processors": [{"id": "guidelines", "plugin": "evolve.guidelines", "config": {"guidelines_model": "old"}}]}
    defaults = {"processors": [{"id": "guidelines", "plugin": "evolve.guidelines", "config": {"guidelines_model": "new"}}]}
    current = {"processors": [{"id": "guidelines", "plugin": "operator.custom", "config": {"threshold": 4}}]}
    assert _refresh_inherited(current, before, defaults) == current


def test_application_replaces_plugin_with_different_config_schema(tmp_path):
    from pydantic import BaseModel, ConfigDict
    from altk_evolve.processing import ProcessorRegistry

    class OldConfig(BaseModel):
        model_config = ConfigDict(extra="forbid")
        old_option: int = 1

    class NewConfig(BaseModel):
        model_config = ConfigDict(extra="forbid")
        new_option: str = "new"

    class OldProcessor:
        id = "test.old"
        version = "1"
        api_version = 1
        config_model = OldConfig

        @classmethod
        def from_config(cls, config):
            return cls()

        def process(self, trajectory, *, context):
            raise AssertionError("refresh must not execute processors")

    class NewProcessor(OldProcessor):
        id = "test.new"
        config_model = NewConfig

    registry = ProcessorRegistry()
    registry.register(OldProcessor)
    registry.register(NewProcessor)
    m = ProcessingManager(registry=registry, repository=SQLiteProfileRepository(tmp_path / "profiles.db"))
    before = {"processors": [{"id": "processor", "plugin": "test.old", "config": {}}]}
    m.ensure("service", before)
    m.put("service", {"processors": [{"id": "processor", "plugin": "test.old", "config": {"old_option": 9}}]}, expected_revision=1)
    result = m.ensure("service", {"processors": [{"id": "processor", "plugin": "test.new", "config": {}}]})
    assert result["manifest"]["processors"][0]["config"] == {"new_option": "new"}
    assert m.resolve("service").manifest() == result["manifest"]


@pytest.mark.parametrize("operator_order,expected", [(["a", "b"], ["b", "new", "a"]), (["b", "a"], ["b", "a", "new"])])
def test_default_order_changes_independently_of_operator_config_edits(tmp_path, operator_order, expected):
    def definition(ids, edited=False):
        return {
            "processors": [
                {"id": key, "plugin": "evolve.guidelines", "config": {"guidelines_mode": "all" if edited and key == "a" else "standard"}}
                for key in ids
            ]
        }

    m = manager(tmp_path / "profiles.db")
    m.ensure("service", definition(["a", "b"]))
    m.put("service", definition(operator_order, edited=True), expected_revision=1)
    result = m.ensure("service", definition(["b", "new", "a"]))
    processors = result["manifest"]["processors"]
    assert [item["id"] for item in processors] == expected
    assert next(item for item in processors if item["id"] == "a")["config"]["guidelines_mode"] == "all"
