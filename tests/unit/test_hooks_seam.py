"""Behavior of the memory hook seam with the CPEX framework installed.

Covers: hook registration, choke-point dispatch on the filesystem backend,
transform/halting semantics, the unified delete path (public +
conflict-resolution DELETE verdicts), the template-method no-bypass
guarantee, the memory_post_read recursion guard, the sync bridge in both
loop states, and the YAML + code-first configuration paths.

Requires the optional cpex package (``uv sync --extra hooks``).
"""

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("cpex")

from cpex.framework import Plugin
from cpex.framework.hooks.registry import get_hook_registry
from cpex.framework.models import OnError, PluginConfig, PluginMode, PluginResult, PluginViolation

from altk_evolve.backend.filesystem import FilesystemEntityBackend
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.hooks import HookPluginSpec, HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.hooks.manager import (
    MemoryPolicyViolation,
    dispatch_llm_pre_call,
    hooks_active,
    initialize_hooks,
    shutdown_hooks,
)
from altk_evolve.hooks.types import HookType, register_evolve_hooks
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity


# ── test plugins ─────────────────────────────────────────────────────


def _config(name: str, hooks: list[str], mode: PluginMode = PluginMode.TRANSFORM, priority: int = 50) -> PluginConfig:
    return PluginConfig(name=name, kind=f"tests.{name}", hooks=hooks, mode=mode, priority=priority, on_error=OnError.FAIL)


class Recorder(Plugin):
    """Records every payload it sees, without modifying anything."""

    def __init__(self, hooks: list[str] | None = None):
        super().__init__(_config("recorder", hooks or [h.value for h in HookType], priority=99))
        self.calls: dict[str, list] = {}

    async def _record(self, hook: str, payload):
        self.calls.setdefault(hook, []).append(payload)
        return PluginResult(continue_processing=True)

    async def memory_pre_write(self, payload, context):
        return await self._record("memory_pre_write", payload)

    async def memory_pre_metadata_patch(self, payload, context):
        return await self._record("memory_pre_metadata_patch", payload)

    async def memory_pre_delete(self, payload, context):
        return await self._record("memory_pre_delete", payload)

    async def memory_pre_namespace_delete(self, payload, context):
        return await self._record("memory_pre_namespace_delete", payload)

    async def memory_post_read(self, payload, context):
        return await self._record("memory_post_read", payload)

    async def llm_pre_call(self, payload, context):
        return await self._record("llm_pre_call", payload)


class UppercaseWriter(Plugin):
    """Transform plugin: uppercases entity content on memory_pre_write."""

    def __init__(self):
        super().__init__(_config("uppercase_writer", [HookType.MEMORY_PRE_WRITE.value], priority=10))

    async def memory_pre_write(self, payload, context):
        entities = [{**e, "content": str(e["content"]).upper()} for e in payload.entities]
        return PluginResult(continue_processing=True, modified_payload=payload.model_copy(update={"entities": entities}))


class Halter(Plugin):
    """Sequential plugin that blocks any payload whose repr contains FORBIDDEN."""

    def __init__(self, hooks: list[str]):
        super().__init__(_config("halter", hooks, mode=PluginMode.SEQUENTIAL, priority=1))

    async def _check(self, payload):
        if "FORBIDDEN" in repr(payload):
            return PluginResult(
                continue_processing=False,
                violation=PluginViolation(reason="forbidden content", description="blocked by test policy", code="TEST_POLICY", details={}),
            )
        return PluginResult(continue_processing=True)

    async def memory_pre_write(self, payload, context):
        return await self._check(payload)

    async def memory_pre_delete(self, payload, context):
        return await self._check(payload)

    async def memory_pre_namespace_delete(self, payload, context):
        return await self._check(payload)


class NestedReader(Plugin):
    """memory_post_read plugin that performs a public read from inside the hook."""

    def __init__(self):
        super().__init__(_config("nested_reader", [HookType.MEMORY_POST_READ.value]))

    async def memory_post_read(self, payload, context):
        backend = context.global_context.state["backend"]
        # Public API read from inside the hook: must NOT re-fire memory_post_read.
        backend.search_entities(payload.namespace_id, limit=5)
        return PluginResult(continue_processing=True)


class MessageTagger(Plugin):
    """Transform plugin for llm_pre_call: prefixes every message with a tag."""

    def __init__(self):
        super().__init__(_config("message_tagger", [HookType.LLM_PRE_CALL.value]))

    async def llm_pre_call(self, payload, context):
        messages = [{**m, "content": f"[tagged:{payload.purpose}] {m['content']}"} for m in payload.messages]
        return PluginResult(continue_processing=True, modified_payload=payload.model_copy(update={"messages": messages}))


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_hook_state():
    shutdown_hooks()
    yield
    shutdown_hooks()


@pytest.fixture
def client(tmp_path: Path) -> EvolveClient:
    return EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))


def enable_hooks(*plugins: Plugin, specs: list[HookPluginSpec] | None = None, plugins_yaml: str | None = None):
    pm = initialize_hooks(HooksConfig(enabled=True, plugins=specs or [], plugins_yaml=plugins_yaml))
    assert pm is not None
    for plugin in plugins:
        pm._registry.register(plugin)
    return pm


def _write(client: EvolveClient, namespace: str, content: str, metadata: dict | None = None) -> None:
    client.update_entities(namespace, [Entity(content=content, type="note", metadata=metadata or {})], enable_conflict_resolution=False)


# ── registration ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_all_hook_types_register_idempotently():
    register_evolve_hooks()
    register_evolve_hooks()  # idempotent
    registry = get_hook_registry()
    for hook_type in HookType:
        assert registry.is_registered(hook_type.value), hook_type


@pytest.mark.unit
def test_hooks_active_requires_a_subscriber():
    enable_hooks(Recorder(hooks=[HookType.MEMORY_PRE_WRITE.value]))
    assert hooks_active(HookType.MEMORY_PRE_WRITE)
    assert not hooks_active(HookType.MEMORY_POST_READ)


# ── choke points (filesystem backend) ────────────────────────────────


@pytest.mark.unit
def test_pre_write_fires_and_transform_applies(client: EvolveClient):
    recorder = Recorder()
    enable_hooks(recorder, UppercaseWriter())
    client.create_namespace("ns")
    _write(client, "ns", "hello world")

    assert len(recorder.calls["memory_pre_write"]) == 1
    assert recorder.calls["memory_pre_write"][0].namespace_id == "ns"
    stored = client.search_entities("ns", limit=10)[0]
    assert stored.content == "HELLO WORLD"


@pytest.mark.unit
def test_pre_write_transform_runs_before_conflict_resolution(client: EvolveClient):
    enable_hooks(UppercaseWriter())
    client.create_namespace("ns")
    seen: list[str] = []

    def fake_resolve_conflicts(old_entities, new_entities):
        seen.extend(str(e.content) for e in new_entities)
        return [EntityUpdate(id=e.id, type=e.type, content=e.content, event="ADD", metadata=e.metadata) for e in new_entities]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", fake_resolve_conflicts):
        client.update_entities("ns", [Entity(content="secret text", type="note")], enable_conflict_resolution=True)

    assert seen == ["SECRET TEXT"]


@pytest.mark.unit
def test_pre_metadata_patch_fires_and_can_transform(client: EvolveClient):
    class PatchAugmenter(Plugin):
        def __init__(self):
            super().__init__(_config("patch_augmenter", [HookType.MEMORY_PRE_METADATA_PATCH.value]))

        async def memory_pre_metadata_patch(self, payload, context):
            return PluginResult(
                continue_processing=True,
                modified_payload=payload.model_copy(update={"metadata_patch": {**payload.metadata_patch, "audited": True}}),
            )

    recorder = Recorder()
    enable_hooks(recorder, PatchAugmenter())
    client.create_namespace("ns")
    _write(client, "ns", "x")
    entity = client.search_entities("ns", limit=1)[0]

    updated = client.patch_entity_metadata("ns", entity.id, {"visibility": "public"})

    assert len(recorder.calls["memory_pre_metadata_patch"]) == 1
    assert recorder.calls["memory_pre_metadata_patch"][0].entity_id == entity.id
    assert updated.metadata["visibility"] == "public"
    assert updated.metadata["audited"] is True


@pytest.mark.unit
def test_pre_delete_and_pre_namespace_delete_fire(client: EvolveClient):
    recorder = Recorder()
    enable_hooks(recorder)
    client.create_namespace("ns")
    _write(client, "ns", "x")
    entity = client.search_entities("ns", limit=1)[0]

    client.delete_entity_by_id("ns", entity.id)
    client.delete_namespace("ns")

    assert len(recorder.calls["memory_pre_delete"]) == 1
    assert recorder.calls["memory_pre_delete"][0].entity_id == entity.id
    assert len(recorder.calls["memory_pre_namespace_delete"]) == 1
    assert recorder.calls["memory_pre_namespace_delete"][0].namespace_id == "ns"


@pytest.mark.unit
def test_post_read_fires_on_public_search_and_can_filter(client: EvolveClient):
    class KeepOnlyPublic(Plugin):
        def __init__(self):
            super().__init__(_config("keep_only_public", [HookType.MEMORY_POST_READ.value]))

        async def memory_post_read(self, payload, context):
            kept = [e for e in payload.entities if (e.get("metadata") or {}).get("visibility") == "public"]
            return PluginResult(continue_processing=True, modified_payload=payload.model_copy(update={"entities": kept}))

    enable_hooks(KeepOnlyPublic())
    client.create_namespace("ns")
    _write(client, "ns", "public one", {"visibility": "public"})
    _write(client, "ns", "private one", {"visibility": "private"})

    results = client.search_entities("ns", limit=10)
    assert [str(r.content) for r in results] == ["public one"]


@pytest.mark.unit
def test_internal_reads_do_not_fire_post_read(client: EvolveClient):
    recorder = Recorder(hooks=[HookType.MEMORY_POST_READ.value])
    enable_hooks(recorder)
    client.create_namespace("ns")
    _write(client, "ns", "x")
    entity = client.search_entities("ns", limit=1)[0]
    assert len(recorder.calls.get("memory_post_read", [])) == 1

    # The metadata-patch read-before-merge is internal: no post_read.
    client.patch_entity_metadata("ns", entity.id, {"k": "v"})
    assert len(recorder.calls.get("memory_post_read", [])) == 1

    # The conflict-resolution pre-read inside update_entities is internal too.
    def fake_resolve_conflicts(old_entities, new_entities):
        return []

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", fake_resolve_conflicts):
        client.update_entities("ns", [Entity(content="y", type="note")], enable_conflict_resolution=True)
    assert len(recorder.calls.get("memory_post_read", [])) == 1


@pytest.mark.unit
def test_post_read_recursion_guard(client: EvolveClient):
    recorder = Recorder(hooks=[HookType.MEMORY_POST_READ.value])
    enable_hooks(recorder, NestedReader())
    client.create_namespace("ns")
    _write(client, "ns", "x")

    results = client.search_entities("ns", limit=10)

    assert len(results) == 1
    # The nested public read inside the plugin did not re-fire the hook.
    assert len(recorder.calls["memory_post_read"]) == 1


# ── halting semantics ────────────────────────────────────────────────


@pytest.mark.unit
def test_halting_write_raises_and_persists_nothing(client: EvolveClient):
    enable_hooks(Halter([HookType.MEMORY_PRE_WRITE.value]))
    client.create_namespace("ns")

    with pytest.raises(MemoryPolicyViolation, match=r"\[TEST_POLICY\] forbidden content"):
        _write(client, "ns", "FORBIDDEN payload")

    assert client.search_entities("ns", limit=10) == []
    # Non-matching content still writes.
    _write(client, "ns", "allowed payload")
    assert len(client.search_entities("ns", limit=10)) == 1


class CrashingWriter(Plugin):
    """Sequential plugin that raises on memory_pre_write (simulates a plugin crash)."""

    def __init__(self):
        super().__init__(_config("crashing_writer", [HookType.MEMORY_PRE_WRITE.value], mode=PluginMode.SEQUENTIAL, priority=1))

    async def memory_pre_write(self, payload, context):
        raise RuntimeError("boom")


class CrashingReader(Plugin):
    """Sequential plugin that raises on memory_post_read (simulates a plugin crash)."""

    def __init__(self):
        super().__init__(_config("crashing_reader", [HookType.MEMORY_POST_READ.value], mode=PluginMode.SEQUENTIAL, priority=1))

    async def memory_post_read(self, payload, context):
        raise RuntimeError("boom")


@pytest.mark.unit
def test_plugin_crash_fails_closed_as_memory_policy_violation(client: EvolveClient):
    # on_error defaults to fail (fail-closed): a plugin that CRASHES on a write
    # must halt the write and surface as MemoryPolicyViolation, never pass the
    # data through — even though it didn't cleanly return continue_processing=False.
    enable_hooks(CrashingWriter())
    client.create_namespace("ns")

    with pytest.raises(MemoryPolicyViolation):
        _write(client, "ns", "some content")

    assert client.search_entities("ns", limit=10) == []


@pytest.mark.unit
def test_post_read_plugin_crash_does_not_fail_the_read(client: EvolveClient):
    # A crash in a post_read plugin (read-side) must NOT fail the read it rode
    # in on: results come back untransformed.
    enable_hooks(CrashingReader())
    client.create_namespace("ns")
    _write(client, "ns", "readable content")

    results = client.search_entities("ns", limit=10)
    assert [str(r.content) for r in results] == ["readable content"]


@pytest.mark.unit
def test_halting_delete_raises_and_preserves_entity(client: EvolveClient):
    enable_hooks(Halter([HookType.MEMORY_PRE_DELETE.value]))
    # The pre-delete payload carries namespace_id + entity_id; the halter
    # matches on the namespace here.
    client.create_namespace("FORBIDDEN_ns")
    _write(client, "FORBIDDEN_ns", "keep me")
    entity = client.search_entities("FORBIDDEN_ns", limit=1)[0]

    with pytest.raises(MemoryPolicyViolation):
        client.delete_entity_by_id("FORBIDDEN_ns", entity.id)

    assert client.get_entity_by_id("FORBIDDEN_ns", entity.id) is not None


# ── unified delete path ──────────────────────────────────────────────
#
# Both delete initiators — the public delete_entity_by_id and LLM DELETE
# verdicts from conflict resolution — route through _guarded_delete, so
# memory_pre_delete fires (with the stored entity's metadata) on every
# entity delete. Veto semantics differ per caller: the public path raises,
# the conflict-resolution executor skips that delete and continues.


class LegalHold(Plugin):
    """Halting plugin: vetoes deletion of entities whose metadata sets legal_hold."""

    def __init__(self):
        super().__init__(_config("legal_hold", [HookType.MEMORY_PRE_DELETE.value], mode=PluginMode.SEQUENTIAL, priority=1))

    async def memory_pre_delete(self, payload, context):
        if (payload.metadata or {}).get("legal_hold"):
            return PluginResult(
                continue_processing=False,
                violation=PluginViolation(
                    reason="entity under legal hold", description="blocked by legal-hold policy", code="LEGAL_HOLD", details={}
                ),
            )
        return PluginResult(continue_processing=True)


def _delete_all_then_add(old_entities, new_entities):
    """Fake resolve_conflicts: DELETE every stored entity, ADD every incoming one."""
    updates = [EntityUpdate(id=e.id, type=e.type, content=e.content, event="DELETE", metadata=e.metadata) for e in old_entities]
    updates += [EntityUpdate(id=e.id, type=e.type, content=e.content, event="ADD", metadata=e.metadata) for e in new_entities]
    return updates


@pytest.mark.unit
def test_cr_delete_verdict_fires_pre_delete_with_stored_metadata(client: EvolveClient):
    recorder = Recorder(hooks=[HookType.MEMORY_PRE_DELETE.value])
    enable_hooks(recorder)
    client.create_namespace("ns")
    _write(client, "ns", "contract with acme", {"case": "c-1"})
    stored = client.search_entities("ns", limit=1)[0]

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", _delete_all_then_add):
        client.update_entities("ns", [Entity(content="contract", type="note")], enable_conflict_resolution=True)

    calls = recorder.calls["memory_pre_delete"]
    # Exactly once per deleted entity — the unified path never double-fires.
    assert len(calls) == 1
    assert calls[0].namespace_id == "ns"
    assert calls[0].entity_id == stored.id
    # Metadata comes from the conflict-resolution pre-read of the STORED entity.
    assert calls[0].metadata == {"case": "c-1"}
    # The delete applied: only the replacement remains.
    assert [str(r.content) for r in client.search_entities("ns", limit=10)] == ["contract"]


@pytest.mark.unit
def test_legal_hold_veto_skips_cr_delete_but_rest_of_batch_applies(client: EvolveClient, caplog):
    enable_hooks(LegalHold())
    client.create_namespace("ns")
    _write(client, "ns", "contract alpha", {"legal_hold": True})
    _write(client, "ns", "contract beta")
    held = client.search_entities("ns", query="alpha", limit=1)[0]
    other = client.search_entities("ns", query="beta", limit=1)[0]

    with (
        patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", _delete_all_then_add),
        caplog.at_level(logging.WARNING, logger="entities-db"),
    ):
        updates = client.update_entities("ns", [Entity(content="contract", type="note")], enable_conflict_resolution=True)

    # The vetoed delete was skipped: the held entity survives alongside its replacement.
    assert client.get_entity_by_id("ns", held.id) is not None
    # The REST of the batch still applied: the non-held delete and the add.
    assert client.get_entity_by_id("ns", other.id) is None
    contents = {str(r.content) for r in client.search_entities("ns", limit=10)}
    assert contents == {"contract alpha", "contract"}
    # The skip is recorded on the returned EntityUpdate and a warning was logged.
    skipped = [u for u in updates if u.id == held.id]
    assert len(skipped) == 1
    assert skipped[0].event == "NONE"
    assert skipped[0].metadata["skipped_delete"]["code"] == "LEGAL_HOLD"
    assert skipped[0].metadata["skipped_delete"]["plugin"] == "legal_hold"
    assert "vetoed conflict-resolution DELETE" in caplog.text
    assert held.id in caplog.text


@pytest.mark.unit
def test_legal_hold_on_external_delete_raises_and_preserves_entity(client: EvolveClient):
    enable_hooks(LegalHold())
    client.create_namespace("ns")
    _write(client, "ns", "keep me", {"legal_hold": True})
    _write(client, "ns", "expendable")
    held = client.search_entities("ns", query="keep me", limit=1)[0]
    other = client.search_entities("ns", query="expendable", limit=1)[0]

    with pytest.raises(MemoryPolicyViolation, match=r"\[LEGAL_HOLD\] entity under legal hold"):
        client.delete_entity_by_id("ns", held.id)

    assert client.get_entity_by_id("ns", held.id) is not None
    # An entity without the hold still deletes through the same guarded path.
    client.delete_entity_by_id("ns", other.id)
    assert client.get_entity_by_id("ns", other.id) is None


@pytest.mark.unit
def test_external_delete_payload_carries_fetched_metadata(client: EvolveClient):
    from altk_evolve.schema.exceptions import EvolveException

    recorder = Recorder(hooks=[HookType.MEMORY_PRE_DELETE.value])
    enable_hooks(recorder)
    client.create_namespace("ns")
    _write(client, "ns", "x", {"case": "c-2"})
    entity = client.search_entities("ns", limit=1)[0]

    client.delete_entity_by_id("ns", entity.id)
    assert len(recorder.calls["memory_pre_delete"]) == 1
    assert recorder.calls["memory_pre_delete"][0].metadata == {"case": "c-2"}

    # Nonexistent id: the hook still fires (metadata=None) and the impl's
    # not-found error surfaces exactly as before.
    with pytest.raises(EvolveException, match="not found"):
        client.delete_entity_by_id("ns", "does-not-exist")
    assert recorder.calls["memory_pre_delete"][1].metadata is None


# ── payload immutability ─────────────────────────────────────────────


class InPlaceMutator(Plugin):
    """Adversarial plugin: mutates the payload's entity dicts in place.

    frozen=True only guards attribute assignment — nested dicts/lists are
    plain mutable objects. Whether the mutation may reach the store must
    depend solely on returning it via ``modified_payload``.
    """

    def __init__(self, return_modified: bool):
        super().__init__(_config("in_place_mutator", [HookType.MEMORY_PRE_WRITE.value]))
        self.return_modified = return_modified

    async def memory_pre_write(self, payload, context):
        payload.entities[0]["content"] = "MUTATED"
        if self.return_modified:
            return PluginResult(continue_processing=True, modified_payload=payload.model_copy(update={"entities": payload.entities}))
        return PluginResult(continue_processing=True)


@pytest.mark.unit
def test_in_place_payload_mutation_does_not_reach_the_store(client: EvolveClient):
    enable_hooks(InPlaceMutator(return_modified=False))
    client.create_namespace("ns")
    _write(client, "ns", "original content")

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.content == "original content"


@pytest.mark.unit
def test_same_mutation_returned_via_modified_payload_applies(client: EvolveClient):
    enable_hooks(InPlaceMutator(return_modified=True))
    client.create_namespace("ns")
    _write(client, "ns", "original content")

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.content == "MUTATED"


# ── sync bridge ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_dispatch_without_running_loop(client: EvolveClient):
    recorder = Recorder(hooks=[HookType.MEMORY_POST_READ.value])
    enable_hooks(recorder)
    client.create_namespace("ns")
    _write(client, "ns", "x")
    assert len(client.search_entities("ns", limit=10)) == 1
    assert len(recorder.calls["memory_post_read"]) == 1


@pytest.mark.unit
def test_dispatch_inside_running_loop(client: EvolveClient):
    recorder = Recorder(hooks=[HookType.MEMORY_POST_READ.value])
    enable_hooks(recorder)
    client.create_namespace("ns")
    _write(client, "ns", "x")

    async def call_sync_api_from_async():
        # Sync client API called while an event loop is running: the bridge
        # must hop to a dedicated thread instead of asyncio.run().
        return client.search_entities("ns", limit=10)

    results = asyncio.run(call_sync_api_from_async())
    assert len(results) == 1
    assert len(recorder.calls["memory_post_read"]) == 1


# ── template method: overrides cannot bypass hooks ───────────────────


@pytest.mark.unit
def test_backend_subclass_override_cannot_bypass_hooks(tmp_path: Path):
    class OverridingBackend(FilesystemEntityBackend):
        """Overrides every _impl seam — the public hooks must still fire."""

        def _search_entities_impl(self, namespace_id, query=None, filters=None, limit=10):
            return super()._search_entities_impl(namespace_id, query, filters, limit)

        def _delete_entity_by_id_impl(self, namespace_id, entity_id):
            return super()._delete_entity_by_id_impl(namespace_id, entity_id)

        def _delete_namespace_impl(self, namespace_id):
            return super()._delete_namespace_impl(namespace_id)

        def _update_entity_metadata_impl(self, namespace_id, entity_id, metadata_patch):
            return super()._update_entity_metadata_impl(namespace_id, entity_id, metadata_patch)

    recorder = Recorder()
    enable_hooks(recorder)
    backend = OverridingBackend(config=FilesystemSettings(data_dir=str(tmp_path)))
    backend.create_namespace("ns")
    backend.update_entities("ns", [Entity(content="x", type="note")], enable_conflict_resolution=False)

    entity = backend.search_entities("ns", limit=1)[0]
    backend.update_entity_metadata("ns", entity.id, {"k": "v"})
    backend.delete_entity_by_id("ns", entity.id)
    backend.delete_namespace("ns")

    assert len(recorder.calls["memory_pre_write"]) == 1
    assert len(recorder.calls["memory_post_read"]) == 1
    assert len(recorder.calls["memory_pre_metadata_patch"]) == 1
    assert len(recorder.calls["memory_pre_delete"]) == 1
    assert len(recorder.calls["memory_pre_namespace_delete"]) == 1


@pytest.mark.unit
def test_backends_do_not_override_public_template_methods():
    template_methods = ("search_entities", "delete_entity_by_id", "_guarded_delete", "delete_namespace", "update_entity_metadata")
    backend_classes = [FilesystemEntityBackend]
    try:
        from altk_evolve.backend.milvus import MilvusEntityBackend

        backend_classes.append(MilvusEntityBackend)
    except ImportError:
        pass
    try:
        from altk_evolve.backend.postgres import PostgresEntityBackend

        backend_classes.append(PostgresEntityBackend)
    except ImportError:
        pass

    for backend_cls in backend_classes:
        for method in template_methods:
            assert method not in vars(backend_cls), f"{backend_cls.__name__}.{method} bypasses the hook template method"


# ── llm_pre_call ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_llm_pre_call_dispatch_transforms_messages():
    enable_hooks(MessageTagger())
    messages = dispatch_llm_pre_call([{"role": "user", "content": "hello"}], purpose="unit_test")
    assert messages == [{"role": "user", "content": "[tagged:unit_test] hello"}]


@pytest.mark.unit
def test_llm_pre_call_fires_at_fact_extraction_call_site():
    from altk_evolve.llm.fact_extraction import fact_extraction

    enable_hooks(MessageTagger())
    response = Mock()
    response.choices = [Mock(message=Mock(content=json.dumps({"facts": ["a fact"]})))]
    with patch.object(fact_extraction, "completion", return_value=response) as mock_completion:
        fact_extraction.extract_facts_from_messages([{"role": "user", "content": "I like tea"}], use_categorization=False)

    sent = mock_completion.call_args.kwargs["messages"]
    assert sent[0]["content"].startswith("[tagged:fact_extraction] ")


# ── configuration paths ──────────────────────────────────────────────


@pytest.mark.unit
def test_code_first_plugin_config(tmp_path: Path):
    config = EvolveConfig(
        backend="filesystem",
        settings=FilesystemSettings(data_dir=str(tmp_path)),
        hooks=HooksConfig(
            enabled=True,
            plugins=[
                HookPluginSpec(
                    name="metadata_normalizer",
                    kind="altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin",
                    hooks=[HookType.MEMORY_PRE_WRITE.value],
                    mode="transform",
                )
            ],
        ),
    )
    client = EvolveClient(config)
    client.create_namespace("ns")
    _write(client, "ns", "x", {"task_id": "t-1"})
    stored = client.search_entities("ns", limit=1)[0]
    assert stored.metadata["trace_id"] == "t-1"


@pytest.mark.unit
def test_yaml_plugin_config(tmp_path: Path):
    plugins_yaml = tmp_path / "plugins.yaml"
    plugins_yaml.write_text(
        """
plugins:
  - name: metadata_normalizer
    kind: altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin
    hooks:
      - memory_pre_write
    mode: transform
    priority: 40
    on_error: ignore
"""
    )
    config = EvolveConfig(
        backend="filesystem",
        settings=FilesystemSettings(data_dir=str(tmp_path / "data")),
        hooks=HooksConfig(enabled=True, plugins_yaml=str(plugins_yaml)),
    )
    client = EvolveClient(config)
    client.create_namespace("ns")
    _write(client, "ns", "x", {"task_id": "t-2"})
    stored = client.search_entities("ns", limit=1)[0]
    assert stored.metadata["trace_id"] == "t-2"
