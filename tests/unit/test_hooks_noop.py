"""No-op guarantees of the memory hook seam.

These tests must pass with OR without the optional cpex package installed:
they cover the default-off behavior (``hooks.enabled=False``) and the
ImportError contract when hooks are enabled without cpex.
"""

from pathlib import Path

import pytest

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.hooks import HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.hooks import manager as hooks_manager
from altk_evolve.hooks import types as hooks_types
from altk_evolve.hooks.manager import (
    dispatch_llm_pre_call,
    dispatch_memory_post_read,
    dispatch_memory_pre_delete,
    dispatch_memory_pre_metadata_patch,
    dispatch_memory_pre_namespace_delete,
    dispatch_memory_pre_write,
    hooks_active,
    initialize_hooks,
    shutdown_hooks,
)
from altk_evolve.hooks.types import HookType
from altk_evolve.schema.core import Entity


@pytest.fixture(autouse=True)
def clean_hook_state():
    shutdown_hooks()
    yield
    shutdown_hooks()


@pytest.fixture
def client(tmp_path: Path) -> EvolveClient:
    return EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))


@pytest.mark.unit
def test_hooks_disabled_by_default():
    config = EvolveConfig()
    assert config.hooks.enabled is False
    assert config.hooks.plugins_yaml is None
    assert config.hooks.plugins == []


@pytest.mark.unit
def test_initialize_returns_none_when_disabled():
    assert initialize_hooks(HooksConfig(enabled=False)) is None
    assert not hooks_active(HookType.MEMORY_PRE_WRITE)


@pytest.mark.unit
def test_dispatch_functions_are_identity_when_disabled(client: EvolveClient):
    backend = client.backend
    entities = [Entity(content="hello", type="note")]
    assert dispatch_memory_pre_write(backend, "ns", entities) is entities

    patch = {"k": "v"}
    assert dispatch_memory_pre_metadata_patch(backend, "ns", "1", patch) is patch

    # Halting-only hooks must simply return.
    dispatch_memory_pre_delete(backend, "ns", "1")
    dispatch_memory_pre_namespace_delete(backend, "ns")

    results: list = []
    assert dispatch_memory_post_read(backend, "ns", results) is results

    messages = [{"role": "user", "content": "hi"}]
    assert dispatch_llm_pre_call(messages, purpose="test") is messages


@pytest.mark.unit
def test_write_read_flow_unchanged_when_disabled(client: EvolveClient):
    client.create_namespace("ns")
    client.update_entities(
        "ns",
        [Entity(content="email bob@example.com", type="note", metadata={"task_id": "t1"})],
        enable_conflict_resolution=False,
    )
    stored = client.search_entities("ns", limit=10)[0]
    assert stored.content == "email bob@example.com"
    # No normalizer/access stamping happened.
    assert stored.metadata == {"task_id": "t1"}

    client.delete_entity_by_id("ns", stored.id)
    assert client.search_entities("ns", limit=10) == []
    client.delete_namespace("ns")
    assert not client.namespace_exists("ns")


@pytest.mark.unit
def test_initialize_raises_without_cpex_when_enabled(monkeypatch):
    monkeypatch.setattr(hooks_manager, "HAS_CPEX", False)
    with pytest.raises(ImportError, match=r"altk-evolve\[hooks\]"):
        initialize_hooks(HooksConfig(enabled=True))
    # Guards stay off after the failed initialization.
    assert not hooks_active(HookType.MEMORY_PRE_WRITE)


@pytest.mark.unit
def test_register_evolve_hooks_noop_without_cpex(monkeypatch):
    monkeypatch.setattr(hooks_types, "HAS_CPEX", False)
    assert hooks_types.register_evolve_hooks() is None
