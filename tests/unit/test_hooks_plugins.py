"""Behavior of the in-tree hook plugins (normalizer, access stamp, PII filter).

Requires the optional cpex package (``uv sync --extra hooks``); the PII tests
additionally require cpex-pii-filter (``--extra pii``).
"""

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("cpex")

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.hooks import HookPluginSpec, HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.hooks.manager import dispatch_llm_pre_call, shutdown_hooks
from altk_evolve.schema.core import Entity

NORMALIZER_SPEC = HookPluginSpec(
    name="metadata_normalizer",
    kind="altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin",
    hooks=["memory_pre_write"],
    mode="transform",
    priority=40,
)
ACCESS_STAMP_SPEC = HookPluginSpec(
    name="access_stamp",
    kind="altk_evolve.hooks.plugins.access_stamp.AccessStampPlugin",
    hooks=["memory_post_read"],
    mode="fire_and_forget",
)
PII_SPEC = HookPluginSpec(
    name="pii_filter_memory",
    kind="altk_evolve.hooks.plugins.pii.PIIFilterMemoryPlugin",
    hooks=["memory_pre_write", "llm_pre_call"],
    mode="transform",
    priority=10,
    config={
        "detect_email": True,
        "detect_ssn": True,
        "detect_phone": True,
        "default_mask_strategy": "redact",
        "redaction_text": "[REDACTED]",
    },
)


@pytest.fixture(autouse=True)
def clean_hook_state():
    shutdown_hooks()
    yield
    shutdown_hooks()


def make_client(tmp_path: Path, *specs: HookPluginSpec) -> EvolveClient:
    config = EvolveConfig(
        backend="filesystem",
        settings=FilesystemSettings(data_dir=str(tmp_path)),
        hooks=HooksConfig(enabled=True, plugins=list(specs)),
    )
    return EvolveClient(config)


# ── MetadataNormalizerPlugin ─────────────────────────────────────────


@pytest.mark.unit
def test_normalizer_copies_task_id_to_trace_id(tmp_path: Path):
    # Why this plugin exists: the MCP server's save_trajectory stamps task_id
    # while Phoenix sync stamps trace_id — downstream cascade cleanup keys on
    # trace_id, so MCP-saved sessions would otherwise miss it.
    client = make_client(tmp_path, NORMALIZER_SPEC)
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="x", type="trajectory", metadata={"task_id": "t-42"})], enable_conflict_resolution=False)

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.metadata["trace_id"] == "t-42"
    assert stored.metadata["task_id"] == "t-42"
    assert "created_at" in stored.metadata


@pytest.mark.unit
def test_normalizer_preserves_existing_values(tmp_path: Path):
    client = make_client(tmp_path, NORMALIZER_SPEC)
    client.create_namespace("ns")
    metadata = {"task_id": "t-1", "trace_id": "existing", "created_at": "2020-01-01T00:00:00+00:00"}
    client.update_entities("ns", [Entity(content="x", type="note", metadata=metadata)], enable_conflict_resolution=False)

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.metadata["trace_id"] == "existing"
    assert stored.metadata["created_at"] == "2020-01-01T00:00:00+00:00"


# ── AccessStampPlugin ────────────────────────────────────────────────


@pytest.mark.unit
def test_access_stamp_records_last_accessed(tmp_path: Path):
    client = make_client(tmp_path, ACCESS_STAMP_SPEC)
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="x", type="note")], enable_conflict_resolution=False)

    first_read = client.search_entities("ns", limit=1)[0]
    assert "last_accessed" not in first_read.metadata  # fire_and_forget: read result itself is untouched

    re_read = client.get_entity_by_id("ns", first_read.id)
    assert re_read is not None
    assert re_read.metadata.get("last_accessed")


@pytest.mark.unit
def test_access_stamp_does_not_loop_with_write_plugins(tmp_path: Path):
    # Stamping goes through the metadata-patch path (internal read + patch),
    # which fires neither memory_post_read nor memory_pre_write — so combining
    # it with write-hook plugins cannot loop.
    client = make_client(tmp_path, NORMALIZER_SPEC, ACCESS_STAMP_SPEC)
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="x", type="note", metadata={"task_id": "t"})], enable_conflict_resolution=False)

    for _ in range(3):
        results = client.search_entities("ns", limit=10)
        assert len(results) == 1

    stored = client.get_entity_by_id("ns", results[0].id)
    assert stored is not None
    assert stored.metadata["trace_id"] == "t"
    assert stored.metadata.get("last_accessed")


# ── PIIFilterMemoryPlugin ────────────────────────────────────────────


@pytest.mark.unit
def test_pii_plugin_redacts_writes(tmp_path: Path):
    pytest.importorskip("cpex_pii_filter")
    client = make_client(tmp_path, PII_SPEC)
    client.create_namespace("ns")
    client.update_entities(
        "ns",
        [Entity(content="email dana@example.com ssn 123-45-6789", type="note")],
        enable_conflict_resolution=False,
    )

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.content == "email [REDACTED] ssn [REDACTED]"


@pytest.mark.unit
def test_pii_plugin_redacts_llm_egress(tmp_path: Path):
    pytest.importorskip("cpex_pii_filter")
    make_client(tmp_path, PII_SPEC)

    messages = dispatch_llm_pre_call([{"role": "user", "content": "reach me at dana@example.com"}], purpose="test")
    assert messages == [{"role": "user", "content": "reach me at [REDACTED]"}]


@pytest.mark.unit
def test_plugin_stubs_raise_without_cpex(monkeypatch):
    import altk_evolve.hooks.plugins.pii as pii_module

    if pii_module._HAS_PII_FILTER:
        pytest.skip("cpex-pii-filter installed; stub not active")
    with pytest.raises(ImportError, match=r"altk-evolve\[pii\]"):
        pii_module.PIIFilterMemoryPlugin()


# ── ReadiSemanticPIIPlugin ───────────────────────────────────────────


class _StubContext:
    """Minimal plugin context: these plugins only ever read global state."""

    class _GC:
        state: dict = {}

    global_context = _GC()


def _pii_plugin():
    from altk_evolve.hooks.plugins.pii import PIIFilterMemoryPlugin

    return PIIFilterMemoryPlugin()


def fake_name_detector(text: str):
    """Stand-in for READI: finds one fixed 'name' so the shim is testable fast.

    Loading a real transformer NER pipeline costs a ~460MB download and seconds
    per test; the detection engine is covered by READI itself and by
    ``examples/pii_benchmark.py``. What these tests must pin is the *shim*:
    config parsing, hook wiring and the modified_payload contract.
    """
    start = text.find("Dana Whitfield")
    return [(start, start + len("Dana Whitfield"))] if start != -1 else []


@pytest.mark.unit
def test_readi_shim_redacts_names_regex_cannot(tmp_path: Path):
    """The point of the semantic plugin: a NAME the regex filter leaves untouched."""
    pytest.importorskip("cpex_pii_filter")
    from altk_evolve.hooks.plugins.readi import ReadiSemanticPIIPlugin
    from altk_evolve.hooks.types import MemoryPreWritePayload

    text = "Dana Whitfield can be reached at dana@example.com"
    payload = MemoryPreWritePayload(namespace_id="ns", entities=[{"content": text, "type": "note"}])

    regex_plugin = _pii_plugin()
    regex_out = asyncio.run(regex_plugin.memory_pre_write(payload, _StubContext()))
    assert "Dana Whitfield" in regex_out.modified_payload.entities[0]["content"]  # regex has no NER

    readi_plugin = ReadiSemanticPIIPlugin()
    readi_plugin._detector = fake_name_detector
    readi_out = asyncio.run(readi_plugin.memory_pre_write(payload, _StubContext()))
    assert readi_out.modified_payload.entities[0]["content"] == "[REDACTED] can be reached at dana@example.com"


@pytest.mark.unit
def test_readi_shim_redacts_llm_egress():
    from altk_evolve.hooks.plugins.readi import ReadiSemanticPIIPlugin
    from altk_evolve.hooks.types import LLMPreCallPayload

    plugin = ReadiSemanticPIIPlugin()
    plugin._detector = fake_name_detector
    payload = LLMPreCallPayload(messages=[{"role": "user", "content": "summarize Dana Whitfield's notes"}], purpose="test")
    result = asyncio.run(plugin.llm_pre_call(payload, _StubContext()))
    assert result.modified_payload.messages == [{"role": "user", "content": "summarize [REDACTED]'s notes"}]


@pytest.mark.unit
def test_readi_shim_reads_config_keys():
    """YAML config drives the mask and the metadata opt-in — no code change needed."""
    from cpex.framework.models import PluginConfig

    from altk_evolve.hooks.plugins.readi import ReadiSemanticPIIPlugin
    from altk_evolve.hooks.types import MemoryPreWritePayload

    plugin = ReadiSemanticPIIPlugin(
        PluginConfig(
            name="readi_semantic_pii",
            kind="altk_evolve.hooks.plugins.readi.ReadiSemanticPIIPlugin",
            hooks=["memory_pre_write"],
            config={"redaction_text": "<PERSON>", "redact_metadata": True},
        )
    )
    plugin._detector = fake_name_detector
    payload = MemoryPreWritePayload(
        namespace_id="ns",
        entities=[{"content": "hi Dana Whitfield", "metadata": {"author": "Dana Whitfield"}}],
    )
    entity = asyncio.run(plugin.memory_pre_write(payload, _StubContext())).modified_payload.entities[0]
    assert entity["content"] == "hi <PERSON>"
    assert entity["metadata"] == {"author": "<PERSON>"}


@pytest.mark.unit
def test_readi_default_config_can_block_and_fails_closed():
    """Mode/on_error are load-bearing, not cosmetic (see docs/guides/memory-hooks.md).

    CPEX downgrades continue_processing=False -> True in transform/audit mode,
    so a redactor that must be able to halt has to register `sequential`; and a
    compliance plugin must fail closed so a crashing NER model never silently
    passes PII through.
    """
    from cpex.framework.models import OnError, PluginMode

    from altk_evolve.hooks.plugins.readi import _default_config

    config = _default_config()
    assert config.mode == PluginMode.SEQUENTIAL
    assert config.on_error == OnError.FAIL
    assert set(config.hooks) == {"memory_pre_write", "llm_pre_call"}


@pytest.mark.unit
def test_readi_shim_stub_raises_without_readi():
    """Without the [readi] extra the shim degrades with a named install hint."""
    import importlib.util

    if importlib.util.find_spec("risk_assessment") is not None:
        pytest.skip("readi-privacy installed; the degradation path is not active")
    from altk_evolve.hooks.plugins.readi import ReadiSemanticPIIPlugin
    from altk_evolve.hooks.types import LLMPreCallPayload

    plugin = ReadiSemanticPIIPlugin()  # construction is cheap; READI loads lazily
    with pytest.raises(ImportError, match=r"altk-evolve\[readi\]"):
        asyncio.run(plugin.llm_pre_call(LLMPreCallPayload(messages=[{"role": "user", "content": "x"}]), _StubContext()))
