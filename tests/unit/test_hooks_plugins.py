"""Behavior of the in-tree hook plugins (normalizer, access stamp, PII filter).

Requires the optional cpex package (``uv sync --extra hooks``); the PII tests
additionally require cpex-pii-filter (``--extra pii``).
"""

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
