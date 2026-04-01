"""
Tests for the Codex platform integration installer behavior.
"""

import json

import pytest


EVOLVE_PLUGIN = "evolve-lite"
EVOLVE_HOOK_SNIPPET = "plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py"


def _marketplace_has_evolve_plugin(path):
    data = json.loads(path.read_text())
    return any(entry.get("name") == EVOLVE_PLUGIN for entry in data.get("plugins", []))


def _hooks_have_evolve_recall(path):
    data = json.loads(path.read_text())
    groups = data.get("hooks", {}).get("UserPromptSubmit", [])
    for group in groups:
        for hook in group.get("hooks", []):
            if EVOLVE_HOOK_SNIPPET in hook.get("command", ""):
                return group.get("matcher") == ""
    return False


@pytest.mark.platform_integrations
class TestCodexInstall:
    """Test the Codex install flow."""

    def test_install_creates_expected_files(self, temp_project_dir, install_runner, file_assertions):
        """Installing Codex should create the plugin tree, marketplace entry, and hook."""
        result = install_runner.run("install", platform="codex")

        plugin_dir = temp_project_dir / "plugins" / EVOLVE_PLUGIN
        file_assertions.assert_dir_exists(plugin_dir)
        file_assertions.assert_file_exists(plugin_dir / ".codex-plugin" / "plugin.json")
        file_assertions.assert_file_exists(plugin_dir / "README.md")
        file_assertions.assert_dir_exists(plugin_dir / "skills" / "learn")
        file_assertions.assert_dir_exists(plugin_dir / "skills" / "recall")
        file_assertions.assert_file_exists(plugin_dir / "skills" / "learn" / "scripts" / "save_entities.py")
        file_assertions.assert_file_exists(plugin_dir / "skills" / "recall" / "scripts" / "retrieve_entities.py")
        file_assertions.assert_file_exists(plugin_dir / "lib" / "entity_io.py")

        marketplace_path = temp_project_dir / ".agents" / "plugins" / "marketplace.json"
        file_assertions.assert_valid_json(marketplace_path)
        assert _marketplace_has_evolve_plugin(marketplace_path), "Evolve plugin entry missing from marketplace.json"

        hooks_path = temp_project_dir / ".codex" / "hooks.json"
        file_assertions.assert_valid_json(hooks_path)
        assert _hooks_have_evolve_recall(hooks_path), "Evolve recall hook missing from .codex/hooks.json"

        hooks_data = json.loads(hooks_path.read_text())
        evolve_groups = [
            group
            for group in hooks_data.get("hooks", {}).get("UserPromptSubmit", [])
            if any(EVOLVE_HOOK_SNIPPET in hook.get("command", "") for hook in group.get("hooks", []))
        ]
        assert evolve_groups[0]["matcher"] == ""
        evolve_hook = next(hook for hook in evolve_groups[0]["hooks"] if EVOLVE_HOOK_SNIPPET in hook.get("command", ""))
        expected_command = (
            'python3 "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py"'
        )
        assert evolve_hook["command"] == expected_command
        assert "~/.codex/config.toml" in result.stdout
        assert "codex_hooks = true" in result.stdout
        assert "evolve-lite:recall" in result.stdout

    def test_codex_dry_run_does_not_write_files(self, temp_project_dir, install_runner):
        """Dry-run should report actions without writing files."""
        result = install_runner.run("install", platform="codex", dry_run=True)

        assert "DRY RUN" in result.stdout
        assert not (temp_project_dir / "plugins" / EVOLVE_PLUGIN).exists()
        assert not (temp_project_dir / ".agents" / "plugins" / "marketplace.json").exists()
        assert not (temp_project_dir / ".codex" / "hooks.json").exists()

    def test_status_reports_codex_installation(self, temp_project_dir, install_runner):
        """Status should show the Codex installation state."""
        install_runner.run("install", platform="codex")
        result = install_runner.run("status")

        assert "Codex:" in result.stdout
        assert "plugins/evolve-lite" in result.stdout
        assert "marketplace.json entry" in result.stdout
        assert ".codex/hooks.json entry" in result.stdout
