"""
Tests for the migration-aware ``uninstall`` path.

An upgrading user still has PRE-REDESIGN ("legacy") artifacts on disk that the
new design never writes. ``uninstall`` must reverse them too, so the user lands
on a true clean slate:

  * Codex (GLOBAL ~/.codex/): legacy plugin registrations in ``config.toml``
    (``[plugins."evolve-lite@<marketplace>"]`` tables) and plugin caches
    (``plugins/cache/<marketplace>/evolve-lite/``).
  * Claude (GLOBAL ~/.claude/): orphan plugin data dirs
    (``plugins/data/evolve-lite-*``) and the ``evolve-marketplace`` registration.
  * Bob: the legacy ``install-evolve-lite`` bootstrap custom mode (a bare YAML
    list item, not a sentinel block).

All removals are defensive, idempotent, and dry-run aware. These tests reuse the
``sandbox_home`` conftest seam (monkeypatches HOME → tmp dir, flows through to
the install.sh subprocess) so we never touch the developer's real home.
"""

import tomllib

import pytest


# ── Codex config.toml fixtures ─────────────────────────────────────────────────

LEGACY_CONFIG_TOML = """\
model = "gpt-5"

[plugins."other@x"]
enabled = true

[plugins."evolve-lite@evolve-marketplace"]
enabled = true
source = "evolve-marketplace"

[plugins."evolve-lite@evolve-local"]
enabled = true
source = "evolve-local"

[history]
persistence = "save-all"
"""


def _seed_legacy_codex(sandbox_home):
    """Write a legacy ~/.codex/config.toml + plugin caches; return key paths."""
    codex = sandbox_home / ".codex"
    config = codex / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(LEGACY_CONFIG_TOML)

    cache = codex / "plugins" / "cache" / "evolve-marketplace"
    (cache / "evolve-lite").mkdir(parents=True, exist_ok=True)
    (cache / "evolve-lite" / "manifest.json").write_text("{}\n")
    (cache / "other-plugin").mkdir(parents=True, exist_ok=True)
    (cache / "other-plugin" / "manifest.json").write_text("{}\n")
    return config, cache


@pytest.mark.platform_integrations
class TestCodexLegacyMigration:
    def test_uninstall_strips_legacy_config_tables(self, sandbox_home, install_runner):
        config, _ = _seed_legacy_codex(sandbox_home)

        install_runner.run("uninstall", platform="codex")

        text = config.read_text()
        assert "evolve-lite@evolve-marketplace" not in text
        assert "evolve-lite@evolve-local" not in text
        # Unrelated tables and top-level keys are preserved.
        assert "other@x" in text
        assert 'model = "gpt-5"' in text
        assert "[history]" in text
        # Result is still valid TOML with no evolve-lite@* plugin key.
        parsed = tomllib.loads(text)
        assert all(not k.startswith("evolve-lite@") for k in parsed.get("plugins", {}))
        assert "other@x" in parsed["plugins"]
        assert parsed["history"]["persistence"] == "save-all"

    def test_uninstall_removes_legacy_plugin_cache(self, sandbox_home, install_runner):
        _, cache = _seed_legacy_codex(sandbox_home)

        install_runner.run("uninstall", platform="codex")

        # evolve-lite subdir gone; its now-empty marketplace parent gone too,
        # BUT only because the sibling other-plugin keeps it alive here.
        assert not (cache / "evolve-lite").exists()
        assert cache.exists(), "marketplace dir with surviving siblings must remain"
        assert (cache / "other-plugin").exists(), "sibling plugin cache preserved"

    def test_uninstall_rmdirs_emptied_marketplace_parent(self, sandbox_home, install_runner):
        codex = sandbox_home / ".codex"
        cache = codex / "plugins" / "cache" / "evolve-local"
        (cache / "evolve-lite").mkdir(parents=True, exist_ok=True)
        (cache / "evolve-lite" / "x.json").write_text("{}\n")

        install_runner.run("uninstall", platform="codex")

        assert not (cache / "evolve-lite").exists()
        assert not cache.exists(), "emptied marketplace parent should be rmdir'd"

    def test_uninstall_no_codex_config_is_noop(self, sandbox_home, install_runner):
        """Absent legacy artifacts: uninstall must not error or create anything."""
        result = install_runner.run("uninstall", platform="codex")
        assert result.returncode == 0
        assert not (sandbox_home / ".codex" / "config.toml").exists()

    def test_uninstall_codex_legacy_is_idempotent(self, sandbox_home, install_runner):
        config, cache = _seed_legacy_codex(sandbox_home)
        install_runner.run("uninstall", platform="codex")
        first = config.read_text()
        # Second run over the already-cleaned state is a clean no-op.
        install_runner.run("uninstall", platform="codex")
        assert config.read_text() == first
        assert not (cache / "evolve-lite").exists()
        assert (cache / "other-plugin").exists()


# ── Claude orphan data dirs + marketplace removal ──────────────────────────────


@pytest.mark.platform_integrations
class TestClaudeLegacyMigration:
    def test_uninstall_removes_orphan_data_dirs(self, sandbox_home, install_runner, temp_project_dir):
        data = sandbox_home / ".claude" / "plugins" / "data"
        for name in ("evolve-lite-inline", "evolve-lite-evolve-marketplace", "other"):
            (data / name).mkdir(parents=True, exist_ok=True)
            (data / name / "store.json").write_text("{}\n")

        install_runner.run("uninstall", platform="claude")

        assert not (data / "evolve-lite-inline").exists()
        assert not (data / "evolve-lite-evolve-marketplace").exists()
        assert (data / "other").exists(), "unrelated plugin data dir preserved"

    def test_uninstall_invokes_marketplace_remove(self, sandbox_home, install_runner, tmp_path):
        """The `claude plugin marketplace remove evolve-marketplace` shell-out is

        attempted. We don't require a real `claude` binary: drop a stub on PATH
        that records its argv, then assert it was called with the remove verb.
        """
        bin_dir = tmp_path / "fakebin"
        bin_dir.mkdir()
        log = tmp_path / "claude_calls.log"
        stub = bin_dir / "claude"
        stub.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{log}"\nexit 0\n')
        stub.chmod(0o755)

        install_runner.run(
            "uninstall",
            platform="claude",
            env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        )

        calls = log.read_text()
        assert "plugin uninstall evolve-lite" in calls
        assert "plugin marketplace remove evolve-marketplace" in calls

    def test_uninstall_removes_legacy_plugin_cache(self, sandbox_home, install_runner, temp_project_dir):
        cache = sandbox_home / ".claude" / "plugins" / "cache" / "evolve-marketplace"
        (cache / "evolve-lite" / "1.1.0").mkdir(parents=True, exist_ok=True)
        (cache / "evolve-lite" / "1.1.0" / "manifest.json").write_text("{}\n")
        (cache / "other-plugin").mkdir(parents=True, exist_ok=True)
        (cache / "other-plugin" / "manifest.json").write_text("{}\n")

        install_runner.run("uninstall", platform="claude")

        # evolve-lite cache subtree gone; its marketplace parent survives because
        # an unrelated sibling plugin cache still lives there.
        assert not (cache / "evolve-lite").exists()
        assert cache.exists(), "marketplace dir with surviving siblings must remain"
        assert (cache / "other-plugin").exists(), "sibling plugin cache preserved"


# ── Bob legacy install-evolve-lite mode ────────────────────────────────────────

LEGACY_BOB_MODES = """\
customModes:
  - slug: install-evolve-lite
    name: Install Evolve Lite
    roleDefinition: |-
      Bootstrap mode. Mentions the sentinel literal # >>>evolve:evolve-lite<<<
      inside its instructions, which must not confuse removal.
    customInstructions: |-
      Run the installer.
    groups:
      - read
      - edit
  - slug: my-mode
    name: My Custom Mode
    roleDefinition: |-
      This is my own mode.
    groups:
      - read
"""


@pytest.mark.platform_integrations
class TestBobLegacyMigration:
    def test_uninstall_removes_legacy_bootstrap_mode(self, temp_project_dir, install_runner):
        modes = temp_project_dir / ".bob" / "custom_modes.yaml"
        modes.parent.mkdir(parents=True, exist_ok=True)
        modes.write_text(LEGACY_BOB_MODES)

        install_runner.run("uninstall", platform="bob")

        text = modes.read_text()
        assert "install-evolve-lite" not in text
        assert "Bootstrap mode" not in text
        # The unrelated user mode survives intact.
        assert "slug: my-mode" in text
        assert "This is my own mode." in text


# ── Dry-run must change nothing on disk ─────────────────────────────────────────


@pytest.mark.platform_integrations
class TestLegacyDryRun:
    def test_dry_run_removes_nothing(self, sandbox_home, install_runner, temp_project_dir):
        config, cache = _seed_legacy_codex(sandbox_home)
        config_before = config.read_text()

        data = sandbox_home / ".claude" / "plugins" / "data"
        (data / "evolve-lite-inline").mkdir(parents=True, exist_ok=True)
        (data / "evolve-lite-inline" / "store.json").write_text("{}\n")

        claude_cache = sandbox_home / ".claude" / "plugins" / "cache" / "evolve-marketplace"
        (claude_cache / "evolve-lite" / "1.1.0").mkdir(parents=True, exist_ok=True)
        (claude_cache / "evolve-lite" / "1.1.0" / "manifest.json").write_text("{}\n")

        modes = temp_project_dir / ".bob" / "custom_modes.yaml"
        modes.parent.mkdir(parents=True, exist_ok=True)
        modes.write_text(LEGACY_BOB_MODES)
        modes_before = modes.read_text()

        result = install_runner.run("uninstall", platform="all", dry_run=True)

        assert result.returncode == 0
        assert "DRY RUN" in result.stdout
        # Nothing on disk changed.
        assert config.read_text() == config_before
        assert (cache / "evolve-lite").exists()
        assert (cache / "other-plugin").exists()
        assert (data / "evolve-lite-inline").exists()
        assert (claude_cache / "evolve-lite").exists()
        assert modes.read_text() == modes_before
