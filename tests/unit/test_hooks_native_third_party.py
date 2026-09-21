"""A third-party NATIVE hook plugin (imports NO cpex) drives the seam end to end.

Proves the decoupling goal: a plugin referenced by ``kind`` that imports only
``altk_evolve.hooks.plugin`` works through a real ``EvolveClient`` / backend with
the engine installed, fails closed on a raise, and coexists with a RAW cpex
plugin (dual support). The last test proves the plugin module pulls no cpex.

Requires the optional cpex package (``uv sync --extra hooks``); the dual-support
test additionally needs cpex-pii-filter (``--extra pii-regex``).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("cpex")

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.hooks import HookPluginSpec, HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.hooks.manager import MemoryPolicyViolation, shutdown_hooks
from altk_evolve.schema.core import Entity

# Top-level import name: tests/unit is on sys.path during collection, so the
# committed sibling module imports as a plain top-level module (no tests.*
# package). This is exactly how a third-party plugin on the path is referenced.
_EXT = "_ext_native_plugin"

TENANT_SPEC = HookPluginSpec(
    name="tenant_tag",
    kind=f"{_EXT}.TenantTagPlugin",
    hooks=["memory_pre_write"],
    mode="transform",
    config={"tenant": "acme"},
)
REJECT_SPEC = HookPluginSpec(
    name="reject_all",
    kind=f"{_EXT}.RejectAllWritesPlugin",
    hooks=["memory_pre_write"],
    mode="sequential",  # sequential so it can halt (transform mode can't block)
)
PII_SPEC = HookPluginSpec(
    name="pii_filter_memory",
    kind="altk_evolve.hooks.plugins.pii.PIIFilterMemoryPlugin",
    hooks=["memory_pre_write"],
    mode="sequential",
    priority=10,
    config={"detect_email": True, "default_mask_strategy": "redact", "redaction_text": "[REDACTED]"},
)


@pytest.fixture(autouse=True)
def clean_hook_state(monkeypatch):
    monkeypatch.setattr("altk_evolve.hooks.manager.discover_hooks_config_path", lambda: None)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent))
    shutdown_hooks()
    yield
    shutdown_hooks()


def _client(tmp_path: Path, *specs: HookPluginSpec) -> EvolveClient:
    return EvolveClient(
        EvolveConfig(
            backend="filesystem",
            settings=FilesystemSettings(data_dir=str(tmp_path)),
            hooks=HooksConfig(plugins=list(specs)),
        )
    )


@pytest.mark.unit
def test_native_third_party_transform_reaches_the_store(tmp_path: Path):
    """(1)+(3) A native plugin's transform reaches the store end to end."""
    client = _client(tmp_path, TENANT_SPEC)
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="hello", type="note")], enable_conflict_resolution=False)

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.metadata["tenant"] == "acme"


@pytest.mark.unit
def test_native_third_party_fails_closed(tmp_path: Path):
    """(2) A native plugin that RAISES fails closed: MemoryPolicyViolation, nothing stored."""
    client = _client(tmp_path, REJECT_SPEC)
    client.create_namespace("ns")

    with pytest.raises(MemoryPolicyViolation):
        client.update_entities("ns", [Entity(content="secret", type="note")], enable_conflict_resolution=False)

    assert client.search_entities("ns", limit=10) == []


@pytest.mark.unit
def test_native_and_raw_cpex_plugins_coexist(tmp_path: Path):
    """(4) A native plugin and a RAW cpex plugin (regex pii) run in the same chain."""
    pytest.importorskip("cpex_pii_filter")
    # pii (priority 10) redacts first, then the native tenant tagger stamps.
    client = _client(tmp_path, PII_SPEC, TENANT_SPEC)
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="mail me at dana@example.com", type="note")], enable_conflict_resolution=False)

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.content == "mail me at [REDACTED]"  # raw cpex plugin redacted
    assert stored.metadata["tenant"] == "acme"  # native plugin tagged


@pytest.mark.unit
def test_ext_native_plugin_module_imports_no_cpex():
    """(5) Importing the third-party plugin module pulls NO cpex, and it references
    only altk_evolve.hooks.plugin / types — verified in a fresh subprocess."""
    code = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})

        import _ext_native_plugin as ext

        assert "cpex" not in sys.modules, "importing the native plugin module pulled cpex"
        # It can be constructed and run with no engine on the path.
        from altk_evolve.hooks.plugin import HookContext
        from altk_evolve.hooks.types import MemoryPreWritePayload
        p = ext.TenantTagPlugin({{"tenant": "acme"}})
        out = p.memory_pre_write(MemoryPreWritePayload(namespace_id="ns", entities=[{{"content": "x"}}]), HookContext())
        assert out.entities[0]["metadata"]["tenant"] == "acme"
        assert "cpex" not in sys.modules, "running the native plugin pulled cpex"
        print("EXT_NO_CPEX_OK")
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "EXT_NO_CPEX_OK" in proc.stdout
