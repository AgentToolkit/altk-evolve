"""Always-on hook seam: config auto-discovery, the deprecated ``enabled``
field, fail-closed engine/detector errors, and the ``evolve hooks init`` CLI.

The discovery + deprecation tests need no optional deps. The tests that build
the engine or exercise READI/regex ``skip`` unless cpex is installed.
"""

from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import pytest
import yaml

from altk_evolve.config.hooks import (
    DEFAULT_HOOKS_CONFIG_FILENAME,
    HOOKS_CONFIG_ENV_VAR,
    HookPluginSpec,
    HooksConfig,
    discover_hooks_config_path,
)
from altk_evolve.hooks import manager as hooks_manager
from altk_evolve.hooks.manager import (
    hooks_active,
    initialize_hooks,
    shutdown_hooks,
)
from altk_evolve.hooks.types import HAS_CPEX, HookType

requires_cpex = pytest.mark.skipif(not HAS_CPEX, reason="requires the [hooks] extra (cpex)")


@pytest.fixture(autouse=True)
def clean_hook_state():
    shutdown_hooks()
    yield
    shutdown_hooks()


# ── deprecated ``enabled`` field ─────────────────────────────────────


@pytest.mark.unit
def test_enabled_is_deprecated_and_ignored():
    """Passing the removed ``enabled`` field warns but does not crash or change
    behavior — the field is popped, config is otherwise normal."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config = HooksConfig(enabled=False, plugins_yaml="x.yaml")
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), caught
    assert not hasattr(config, "enabled")
    # The rest of the config is unaffected.
    assert config.plugins_yaml == "x.yaml"


@pytest.mark.unit
def test_config_without_enabled_does_not_warn():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        HooksConfig(plugins_yaml="x.yaml")
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught), caught


# ── config auto-discovery ────────────────────────────────────────────


@pytest.mark.unit
def test_discovery_env_var_wins(tmp_path: Path):
    explicit = tmp_path / "custom.yaml"
    explicit.write_text("plugins: []\n")
    (tmp_path / DEFAULT_HOOKS_CONFIG_FILENAME).write_text("plugins: []\n")
    found = discover_hooks_config_path(env={HOOKS_CONFIG_ENV_VAR: str(explicit)}, cwd=tmp_path, user_config_dir=tmp_path)
    assert found == str(explicit)


@pytest.mark.unit
def test_discovery_project_local(tmp_path: Path):
    local = tmp_path / DEFAULT_HOOKS_CONFIG_FILENAME
    local.write_text("plugins: []\n")
    found = discover_hooks_config_path(env={}, cwd=tmp_path, user_config_dir=tmp_path / "nope")
    assert found == str(local)


@pytest.mark.unit
def test_discovery_user_config_dir(tmp_path: Path):
    user_dir = tmp_path / "cfg"
    (user_dir / "evolve").mkdir(parents=True)
    user_cfg = user_dir / "evolve" / "hooks.yaml"
    user_cfg.write_text("plugins: []\n")
    found = discover_hooks_config_path(env={}, cwd=tmp_path / "empty", user_config_dir=user_dir)
    assert found == str(user_cfg)


@pytest.mark.unit
def test_discovery_nothing_found_returns_none(tmp_path: Path):
    assert discover_hooks_config_path(env={}, cwd=tmp_path, user_config_dir=tmp_path / "none") is None


@pytest.mark.unit
def test_discovery_order_project_over_user(tmp_path: Path):
    (tmp_path / DEFAULT_HOOKS_CONFIG_FILENAME).write_text("plugins: []\n")
    user_dir = tmp_path / "cfg"
    (user_dir / "evolve").mkdir(parents=True)
    (user_dir / "evolve" / "hooks.yaml").write_text("plugins: []\n")
    found = discover_hooks_config_path(env={}, cwd=tmp_path, user_config_dir=user_dir)
    assert found == str(tmp_path / DEFAULT_HOOKS_CONFIG_FILENAME)


@requires_cpex
@pytest.mark.unit
def test_discovered_config_loads_plugins(tmp_path: Path, monkeypatch):
    """A discovered evolve.hooks.yaml loads its plugins through initialize_hooks."""
    cfg = tmp_path / DEFAULT_HOOKS_CONFIG_FILENAME
    cfg.write_text(
        "plugins:\n"
        "  - name: metadata_normalizer\n"
        "    kind: altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin\n"
        "    hooks: [memory_pre_write]\n"
        "    mode: transform\n"
    )
    monkeypatch.setattr(hooks_manager, "discover_hooks_config_path", lambda: str(cfg))
    pm = initialize_hooks(HooksConfig())  # plugins_yaml unset → discovery kicks in
    assert pm is not None
    assert hooks_active(HookType.MEMORY_PRE_WRITE)


@requires_cpex
@pytest.mark.unit
def test_explicit_plugins_yaml_overrides_discovery(tmp_path: Path, monkeypatch):
    """An explicit plugins_yaml wins over a would-be discovered file."""
    discovered = tmp_path / DEFAULT_HOOKS_CONFIG_FILENAME
    discovered.write_text("plugins: []\n")  # empty
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        "plugins:\n"
        "  - name: metadata_normalizer\n"
        "    kind: altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin\n"
        "    hooks: [memory_pre_write]\n"
        "    mode: transform\n"
    )
    monkeypatch.setattr(hooks_manager, "discover_hooks_config_path", lambda: str(discovered))
    pm = initialize_hooks(HooksConfig(plugins_yaml=str(explicit)))
    assert pm is not None
    assert hooks_active(HookType.MEMORY_PRE_WRITE)


@requires_cpex
@pytest.mark.unit
def test_code_first_plugins_suppress_discovery(tmp_path: Path, monkeypatch):
    """Code-first ``plugins`` (no ``plugins_yaml``) must override discovery:
    ``discover_hooks_config_path`` is never consulted and a stray discoverable
    file's plugins are NOT merged — only the code-first plugin is registered."""
    # A stray discoverable file that, if consulted, would register a DIFFERENTLY
    # named plugin — its presence lets us prove it was neither read nor merged.
    stray = tmp_path / DEFAULT_HOOKS_CONFIG_FILENAME
    stray.write_text(
        "plugins:\n"
        "  - name: stray_from_discovery\n"
        "    kind: altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin\n"
        "    hooks: [memory_pre_write]\n"
        "    mode: transform\n"
    )
    monkeypatch.setenv(HOOKS_CONFIG_ENV_VAR, str(stray))

    # Spy: flip a flag (and still return the stray path) if discovery is
    # consulted at all — the fix must make this unreachable.
    called = False

    def _spy():
        nonlocal called
        called = True
        return str(stray)

    monkeypatch.setattr(hooks_manager, "discover_hooks_config_path", _spy)

    spec = HookPluginSpec(
        name="code_first_normalizer",
        kind="altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin",
        hooks=[HookType.MEMORY_PRE_WRITE.value],
        mode="transform",
    )
    pm = initialize_hooks(HooksConfig(plugins=[spec]))
    assert pm is not None
    assert not called, "discover_hooks_config_path was consulted despite code-first plugins"
    # Only the code-first plugin is registered; the stray file was not merged.
    registered = {ref.name for ref in pm._registry.get_all_plugins()}
    assert registered == {"code_first_normalizer"}, registered


@pytest.mark.unit
def test_discovery_nothing_found_is_noop(monkeypatch):
    monkeypatch.setattr(hooks_manager, "discover_hooks_config_path", lambda: None)
    assert initialize_hooks(HooksConfig()) is None
    assert not hooks_active(HookType.MEMORY_PRE_WRITE)


# ── fail-closed on missing detector lib ──────────────────────────────


@requires_cpex
@pytest.mark.unit
def test_configured_readi_without_readi_raises_pii_semantic(monkeypatch):
    """READI configured but the READI lib missing must fail closed at INIT,
    naming the [pii-semantic] extra — not lazily on the first write."""
    import altk_evolve.hooks.plugins.readi as readi

    def _boom(**_kwargs):
        raise ImportError("Semantic PII redaction requires IBM READI. Install it with: pip install 'altk-evolve[pii-semantic]'")

    monkeypatch.setattr(readi, "build_readi_detector", _boom)
    monkeypatch.setattr(hooks_manager, "discover_hooks_config_path", lambda: None)
    spec = HookPluginSpec(
        name="readi",
        kind="altk_evolve.hooks.plugins.readi.ReadiSemanticPIIPlugin",
        hooks=[HookType.MEMORY_PRE_WRITE.value],
        mode="sequential",
    )
    with pytest.raises(ImportError, match=r"altk-evolve\[pii-semantic\]"):
        initialize_hooks(HooksConfig(plugins=[spec]))
    assert not hooks_active(HookType.MEMORY_PRE_WRITE)


# ── no-op path never imports cpex (subprocess with cpex blocked) ─────


@pytest.mark.unit
def test_no_plugin_flow_does_not_import_cpex(tmp_path: Path):
    """A backend used with no hooks config must neither error nor import cpex,
    even when cpex is made unimportable."""
    script = (
        "import sys, importlib.abc, importlib.machinery\n"
        "class Blocker(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path, target=None):\n"
        "        if name == 'cpex' or name.startswith('cpex.'):\n"
        "            raise ImportError('cpex blocked for test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "from altk_evolve.config.evolve import EvolveConfig\n"
        "from altk_evolve.config.filesystem import FilesystemSettings\n"
        "from altk_evolve.frontend.client.evolve_client import EvolveClient\n"
        f"c = EvolveClient(config=EvolveConfig(backend='filesystem', settings=FilesystemSettings(data_dir={str(tmp_path / 'd')!r})))\n"
        "c.create_namespace('ns')\n"
        "from altk_evolve.schema.core import Entity\n"
        "c.update_entities('ns', [Entity(content='hi', type='note')], enable_conflict_resolution=False)\n"
        "assert c.search_entities('ns', limit=5)[0].content == 'hi'\n"
        "assert 'cpex' not in sys.modules, 'cpex was imported on the no-op path'\n"
        "print('NOOP_OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "NOOP_OK" in proc.stdout


# ── evolve hooks init CLI ────────────────────────────────────────────


@pytest.mark.unit
def test_hooks_init_platform_note_macos_vs_other():
    from altk_evolve.cli.cli import hooks_init_platform_note

    mac = hooks_init_platform_note("Darwin")
    other = hooks_init_platform_note("Linux")
    assert "MPS" in mac and "macOS" in mac
    assert "MPS" not in other
    assert "out of the box" in other


@pytest.mark.unit
def test_hooks_init_writes_readi_active_regex_commented(tmp_path: Path):
    from typer.testing import CliRunner

    from altk_evolve.cli.cli import app

    target = tmp_path / DEFAULT_HOOKS_CONFIG_FILENAME
    result = CliRunner().invoke(app, ["hooks", "init", "--path", str(target)])
    assert result.exit_code == 0, result.output
    assert target.exists()

    text = target.read_text()
    # READI block is ACTIVE (uncommented plugin entry).
    assert "- name: readi_semantic_pii" in text
    # Regex block is COMMENTED OUT.
    assert "# - name: pii_filter_memory" in text
    assert "\n  - name: pii_filter_memory" not in text
    # Both carry the load-bearing sequential + fail settings.
    assert "mode: sequential" in text
    assert "on_error: fail" in text

    # Post-init guidance mentions the extra and auto-discovery.
    assert "altk-evolve[pii-semantic]" in result.output
    assert "auto-discovers" in result.output


@pytest.mark.unit
def test_hooks_init_refuses_to_clobber_then_force(tmp_path: Path):
    from typer.testing import CliRunner

    from altk_evolve.cli.cli import app

    target = tmp_path / DEFAULT_HOOKS_CONFIG_FILENAME
    runner = CliRunner()
    assert runner.invoke(app, ["hooks", "init", "--path", str(target)]).exit_code == 0
    # Second run without --force refuses.
    clobber = runner.invoke(app, ["hooks", "init", "--path", str(target)])
    assert clobber.exit_code == 1
    assert "Refusing to overwrite" in clobber.output
    # With --force it overwrites.
    assert runner.invoke(app, ["hooks", "init", "--path", str(target), "--force"]).exit_code == 0


@requires_cpex
@pytest.mark.unit
def test_hooks_init_output_round_trips_through_loader(tmp_path: Path, monkeypatch):
    """The scaffolded file is valid YAML AND loads its (READI) plugin through
    the engine loader."""
    from typer.testing import CliRunner

    from altk_evolve.cli.cli import app

    target = tmp_path / DEFAULT_HOOKS_CONFIG_FILENAME
    assert CliRunner().invoke(app, ["hooks", "init", "--path", str(target)]).exit_code == 0

    # Valid YAML with exactly the READI plugin active.
    parsed = yaml.safe_load(target.read_text())
    names = [p["name"] for p in parsed["plugins"]]
    assert names == ["readi_semantic_pii"]

    monkeypatch.setattr(hooks_manager, "discover_hooks_config_path", lambda: None)
    pm = initialize_hooks(HooksConfig(plugins_yaml=str(target)))
    assert pm is not None
    assert hooks_active(HookType.MEMORY_PRE_WRITE)
