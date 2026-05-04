"""
Tests to ensure install.sh is idempotent - running it multiple times is safe.
"""

import json

import pytest


@pytest.mark.platform_integrations
class TestBobIdempotency:
    """Test that Bob installation is idempotent."""

    def test_multiple_lite_installs(self, temp_project_dir, install_runner, file_assertions):
        """Running install twice for Bob lite mode should be safe."""
        # First install
        install_runner.run("install", platform="bob", mode="lite")

        # Capture state after first install
        bob_dir = temp_project_dir / ".bob"
        custom_modes_file = bob_dir / "custom_modes.yaml"
        first_content = custom_modes_file.read_text()

        # Second install
        install_runner.run("install", platform="bob", mode="lite")

        # Assert: Files are identical
        second_content = custom_modes_file.read_text()
        assert first_content == second_content, "Content changed after second install"

        # Assert: No duplicate sentinel blocks
        assert first_content.count("# >>>evolve:evolve-lite<<<") == 1
        assert first_content.count("# <<<evolve:evolve-lite<<<") == 1

        # Assert: All skills still exist
        file_assertions.assert_all_bob_skills_installed(bob_dir)

    def test_multiple_full_installs(self, temp_project_dir, install_runner, file_assertions):
        """Running install twice for Bob full mode should be safe."""
        # First install
        install_runner.run("install", platform="bob", mode="full")

        # Capture state after first install
        bob_dir = temp_project_dir / ".bob"
        mcp_file = bob_dir / "mcp.json"
        first_data = json.loads(mcp_file.read_text())

        # Second install
        install_runner.run("install", platform="bob", mode="full")

        # Assert: MCP config is identical
        second_data = json.loads(mcp_file.read_text())
        assert first_data == second_data, "MCP config changed after second install"

        # Assert: Only one evolve server entry
        assert "evolve" in second_data["mcpServers"]
        assert len([k for k in second_data["mcpServers"].keys() if k == "evolve"]) == 1

    def test_install_after_partial_uninstall(self, temp_project_dir, install_runner, file_assertions):
        """Installing after manually deleting some components should restore them."""
        # Initial install
        install_runner.run("install", platform="bob")

        bob_dir = temp_project_dir / ".bob"

        # Manually delete one skill
        import shutil

        shutil.rmtree(bob_dir / "skills" / "evolve-lite-learn")

        # Reinstall
        install_runner.run("install", platform="bob")

        # Assert: All skills are restored
        file_assertions.assert_all_bob_skills_installed(bob_dir)
        file_assertions.assert_file_exists(bob_dir / "custom_modes.yaml")


@pytest.mark.platform_integrations
class TestBobLegacyMigration:
    """Upgrade path from the pre-rename `evolve-lite:<name>` colon-form layout."""

    def _seed_legacy_artifacts(self, bob_dir):
        """Drop a stale colon-form skill + command into .bob/, the way an old install left it."""
        legacy_skill = bob_dir / "skills" / "evolve-lite:learn"
        legacy_skill.mkdir(parents=True)
        (legacy_skill / "SKILL.md").write_text("legacy colon-form skill\n")
        legacy_cmd = bob_dir / "commands" / "evolve-lite:learn.md"
        legacy_cmd.parent.mkdir(parents=True, exist_ok=True)
        legacy_cmd.write_text("legacy colon-form command\n")
        return legacy_skill, legacy_cmd

    def test_install_purges_legacy_colon_form(self, temp_project_dir, install_runner, file_assertions):
        """Re-running install over a pre-rename layout wipes the legacy artifacts.

        Reproduces visahak's PR #235 finding: without the purge, `.bob/skills/`
        ends up with both `evolve-lite:learn` and `evolve-lite-learn` after
        upgrade.
        """
        bob_dir = temp_project_dir / ".bob"
        legacy_skill, legacy_cmd = self._seed_legacy_artifacts(bob_dir)

        install_runner.run("install", platform="bob", mode="lite")

        # Legacy artifacts gone
        assert not legacy_skill.exists(), "legacy colon-form skill survived install"
        assert not legacy_cmd.exists(), "legacy colon-form command survived install"
        # Current dash-form layout in place
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-lite-learn")
        file_assertions.assert_file_exists(bob_dir / "commands" / "evolve-lite-learn.md")

    def test_uninstall_purges_legacy_colon_form(self, temp_project_dir, install_runner, file_assertions):
        """Uninstall removes legacy colon-form stragglers alongside the dash-form."""
        install_runner.run("install", platform="bob", mode="lite")
        bob_dir = temp_project_dir / ".bob"

        # Inject legacy artifacts post-install — simulates an upgrade gap where
        # a user moved through several versions and accumulated both forms.
        legacy_skill, legacy_cmd = self._seed_legacy_artifacts(bob_dir)

        install_runner.run("uninstall", platform="bob")

        assert not legacy_skill.exists(), "uninstall left legacy colon-form skill behind"
        assert not legacy_cmd.exists(), "uninstall left legacy colon-form command behind"
        file_assertions.assert_dir_not_exists(bob_dir / "skills" / "evolve-lite-learn")
        file_assertions.assert_file_not_exists(bob_dir / "commands" / "evolve-lite-learn.md")

    def test_install_preserves_user_content_during_legacy_purge(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """The legacy purge MUST NOT clobber non-evolve user skills/commands."""
        bob_dir = temp_project_dir / ".bob"
        custom_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        custom_command = bob_fixtures.create_existing_command(temp_project_dir)
        legacy_skill, _ = self._seed_legacy_artifacts(bob_dir)

        install_runner.run("install", platform="bob", mode="lite")

        # Legacy purged, user content intact.
        assert not legacy_skill.exists()
        file_assertions.assert_dir_exists(custom_skill)
        file_assertions.assert_file_exists(custom_command)


@pytest.mark.platform_integrations
class TestCodexIdempotency:
    """Test that Codex installation is idempotent."""

    def test_multiple_installs(self, temp_project_dir, install_runner, file_assertions):
        """Running install twice for Codex should be safe."""
        install_runner.run("install", platform="codex")

        marketplace_file = temp_project_dir / ".agents" / "plugins" / "marketplace.json"
        hooks_file = temp_project_dir / ".codex" / "hooks.json"
        first_marketplace = json.loads(marketplace_file.read_text())
        first_hooks = json.loads(hooks_file.read_text())

        install_runner.run("install", platform="codex")

        second_marketplace = json.loads(marketplace_file.read_text())
        second_hooks = json.loads(hooks_file.read_text())

        assert first_marketplace == second_marketplace, "marketplace.json changed after second install"
        assert first_hooks == second_hooks, ".codex/hooks.json changed after second install"

        evolve_plugins = [entry for entry in second_marketplace["plugins"] if entry["name"] == "evolve-lite"]
        assert len(evolve_plugins) == 1, "Duplicate evolve-lite marketplace entries found"

        prompt_hooks = second_hooks["hooks"]["UserPromptSubmit"]
        evolve_hook_groups = [
            group
            for group in prompt_hooks
            if any(
                "plugins/evolve-lite/skills/evolve-lite/recall/scripts/retrieve_entities.py" in hook.get("command", "")
                for hook in group.get("hooks", [])
            )
        ]
        assert len(evolve_hook_groups) == 1, "Duplicate Evolve UserPromptSubmit hooks found"
        assert evolve_hook_groups[0].get("matcher") == ""

    def test_install_after_partial_uninstall(self, temp_project_dir, install_runner, file_assertions):
        """Installing after deleting part of the Codex plugin should restore it."""
        install_runner.run("install", platform="codex")

        plugin_dir = temp_project_dir / "plugins" / "evolve-lite"

        import shutil

        shutil.rmtree(plugin_dir / "skills" / "evolve-lite" / "learn")

        install_runner.run("install", platform="codex")

        file_assertions.assert_dir_exists(plugin_dir / "skills" / "evolve-lite" / "learn")
        file_assertions.assert_file_exists(plugin_dir / "skills" / "evolve-lite" / "learn" / "SKILL.md")
        file_assertions.assert_file_exists(plugin_dir / "lib" / "entity_io.py")


@pytest.mark.platform_integrations
class TestUninstallInstallCycle:
    """Test that uninstall followed by install works correctly."""

    def test_bob_uninstall_install_cycle(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Uninstalling and reinstalling Bob should work correctly."""
        # Create user content
        bob_fixtures.create_existing_skill(temp_project_dir)
        bob_fixtures.create_existing_custom_modes(temp_project_dir)

        # Install
        install_runner.run("install", platform="bob")

        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-lite-learn")

        # Uninstall
        install_runner.run("uninstall", platform="bob")

        file_assertions.assert_dir_not_exists(bob_dir / "skills" / "evolve-lite-learn")
        file_assertions.assert_dir_not_exists(bob_dir / "skills" / "evolve-lite-recall")

        # Reinstall
        install_runner.run("install", platform="bob")

        # Assert: Evolve content is back
        file_assertions.assert_all_bob_skills_installed(bob_dir)
        file_assertions.assert_sentinel_block_exists(bob_dir / "custom_modes.yaml", "evolve-lite")

        # Assert: User content still intact
        file_assertions.assert_dir_exists(bob_dir / "skills" / "my-custom-skill")
        custom_modes = (bob_dir / "custom_modes.yaml").read_text()
        assert "slug: my-mode" in custom_modes

    def test_codex_uninstall_install_cycle(self, temp_project_dir, install_runner, codex_fixtures, file_assertions):
        """Uninstalling and reinstalling Codex should work correctly."""
        custom_plugin = codex_fixtures.create_existing_plugin(temp_project_dir)
        marketplace_file = codex_fixtures.create_existing_marketplace(temp_project_dir)
        hooks_file = codex_fixtures.create_existing_hooks(temp_project_dir)

        plugin_json = custom_plugin / ".codex-plugin" / "plugin.json"
        original_plugin_content = plugin_json.read_text()

        install_runner.run("install", platform="codex")

        evolve_plugin_dir = temp_project_dir / "plugins" / "evolve-lite"
        file_assertions.assert_dir_exists(evolve_plugin_dir)

        install_runner.run("uninstall", platform="codex")

        file_assertions.assert_dir_not_exists(evolve_plugin_dir)
        current_marketplace = json.loads(marketplace_file.read_text())
        assert all(entry["name"] != "evolve-lite" for entry in current_marketplace["plugins"])

        current_hooks = json.loads(hooks_file.read_text())
        prompt_hooks = current_hooks["hooks"].get("UserPromptSubmit", [])
        evolve_hooks = [
            hook
            for group in prompt_hooks
            for hook in group.get("hooks", [])
            if "plugins/evolve-lite/skills/evolve-lite/recall/scripts/retrieve_entities.py" in hook.get("command", "")
        ]
        assert not evolve_hooks, "Evolve hook still present after uninstall"

        install_runner.run("install", platform="codex")

        file_assertions.assert_dir_exists(evolve_plugin_dir)
        file_assertions.assert_file_unchanged(plugin_json, original_plugin_content)

        reinstalled_marketplace = json.loads(marketplace_file.read_text())
        assert any(entry["name"] == "my-codex-plugin" for entry in reinstalled_marketplace["plugins"])
        assert any(entry["name"] == "evolve-lite" for entry in reinstalled_marketplace["plugins"])

        reinstalled_hooks = json.loads(hooks_file.read_text())
        assert any(
            hook.get("command") == "python3 ~/.codex/hooks/custom_prompt_memory.py"
            for group in reinstalled_hooks["hooks"]["UserPromptSubmit"]
            for hook in group.get("hooks", [])
        )
        assert any(
            "plugins/evolve-lite/skills/evolve-lite/recall/scripts/retrieve_entities.py" in hook.get("command", "")
            for group in reinstalled_hooks["hooks"]["UserPromptSubmit"]
            for hook in group.get("hooks", [])
        )
