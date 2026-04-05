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
        for hook in _iter_group_hooks(group):
            if EVOLVE_HOOK_SNIPPET in hook.get("command", ""):
                return group.get("matcher") == ""
    return False


def _iter_group_hooks(group):
    hooks = group.get("hooks", [])
    if isinstance(hooks, list):
        return hooks
    if isinstance(hooks, dict):
        return list(hooks.values())
    return []


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
            "sh -lc '"
            'd="$PWD"; '
            "while :; do "
            'candidate="$d/plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py"; '
            'if [ -f "$candidate" ]; then EVOLVE_DIR="$d/.evolve" exec python3 "$candidate"; fi; '
            '[ "$d" = "/" ] && break; '
            'd="$(dirname "$d")"; '
            "done; "
            "exit 1'"
        )
        assert evolve_hook["command"] == expected_command
        assert "~/.codex/config.toml" in result.stdout
        assert "codex_hooks = true" in result.stdout
        assert "evolve-lite:recall" in result.stdout

    def test_install_preserves_matching_user_prompt_group(self, temp_project_dir, install_runner, codex_fixtures):
        """Installing should merge the evolve hook into an existing matching list-based group."""
        hooks_path = codex_fixtures.create_existing_hooks_with_shared_evolve_group(temp_project_dir)

        install_runner.run("install", platform="codex")

        hooks_data = json.loads(hooks_path.read_text())
        prompt_groups = hooks_data["hooks"]["UserPromptSubmit"]
        assert len(prompt_groups) == 1

        merged_group = prompt_groups[0]
        assert merged_group["matcher"] == "src/.*"

        custom_hooks = [
            hook for hook in _iter_group_hooks(merged_group) if hook.get("command") == "python3 ~/.codex/hooks/custom_prompt_memory.py"
        ]
        assert len(custom_hooks) == 1, "Custom prompt hook was removed from the shared group"

        evolve_hooks = [hook for hook in _iter_group_hooks(merged_group) if EVOLVE_HOOK_SNIPPET in hook.get("command", "")]
        assert len(evolve_hooks) == 1, "Evolve hook was duplicated or removed from the shared group"
        assert evolve_hooks[0]["statusMessage"] == "Loading Evolve guidance"
        assert evolve_hooks[0]["delayMs"] == 250

    def test_install_updates_dict_based_matching_group(self, temp_project_dir, install_runner, codex_fixtures):
        """Installing should update a dict-based matching group without adding a replacement group."""
        hooks_path = codex_fixtures.create_existing_hooks_with_dict_evolve_group(temp_project_dir)

        install_runner.run("install", platform="codex")

        hooks_data = json.loads(hooks_path.read_text())
        prompt_groups = hooks_data["hooks"]["UserPromptSubmit"]
        assert len(prompt_groups) == 1

        merged_group = prompt_groups[0]
        assert merged_group["matcher"] == "src/.*"
        assert isinstance(merged_group["hooks"], dict)
        assert "memory" in merged_group["hooks"]
        assert "evolve-lite" in merged_group["hooks"]

        evolve_hook = merged_group["hooks"]["evolve-lite"]
        assert EVOLVE_HOOK_SNIPPET in evolve_hook["command"]
        assert evolve_hook["statusMessage"] == "Loading Evolve guidance"
        assert evolve_hook["delayMs"] == 250

    def test_uninstall_removes_only_evolve_hook_from_matching_group(self, temp_project_dir, install_runner, codex_fixtures):
        """Uninstalling should remove only the evolve hook entry and preserve the shared group."""
        hooks_path = codex_fixtures.create_existing_hooks_with_dict_evolve_group(temp_project_dir)

        install_runner.run("uninstall", platform="codex")

        hooks_data = json.loads(hooks_path.read_text())
        prompt_groups = hooks_data["hooks"]["UserPromptSubmit"]
        assert len(prompt_groups) == 1

        remaining_group = prompt_groups[0]
        assert remaining_group["matcher"] == "src/.*"
        assert isinstance(remaining_group["hooks"], dict)
        assert "memory" in remaining_group["hooks"]
        assert "evolve-lite" not in remaining_group["hooks"]
        assert all(EVOLVE_HOOK_SNIPPET not in hook.get("command", "") for hook in _iter_group_hooks(remaining_group))

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
