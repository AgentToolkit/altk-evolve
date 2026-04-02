"""
Critical tests to ensure install.sh NEVER overwrites existing user data.

These are the most important tests - they verify that user's custom skills,
commands, modes, and configurations are preserved during installation.
"""

import json
import pytest


@pytest.mark.platform_integrations
class TestBobPreservation:
    """Test that Bob installation preserves existing user data."""

    def test_preserves_existing_skills(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install evolve when user has existing custom skills - they must be preserved."""
        # Setup: Create user's custom skill
        custom_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        original_content = (custom_skill / "SKILL.md").read_text()

        # Action: Install evolve
        install_runner.run("install", platform="bob")

        # Assert: User's skill is untouched
        file_assertions.assert_dir_exists(custom_skill)
        file_assertions.assert_file_unchanged(custom_skill / "SKILL.md", original_content)

        # Assert: Evolve skills are added
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-learn")
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-recall")

    def test_preserves_existing_commands(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install evolve when user has existing commands - they must be preserved."""
        # Setup: Create user's custom command
        custom_command = bob_fixtures.create_existing_command(temp_project_dir)
        original_content = custom_command.read_text()

        # Action: Install evolve
        install_runner.run("install", platform="bob")

        # Assert: User's command is untouched
        file_assertions.assert_file_unchanged(custom_command, original_content)

        # Assert: Evolve commands are added
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_file_exists(bob_dir / "commands" / "evolve:learn.md")
        file_assertions.assert_file_exists(bob_dir / "commands" / "evolve:recall.md")

    def test_preserves_existing_custom_modes_yaml(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install evolve when user has existing custom modes - they must be preserved."""
        # Setup: Create user's custom mode
        custom_modes_file = bob_fixtures.create_existing_custom_modes(temp_project_dir)

        # Action: Install evolve
        install_runner.run("install", platform="bob")

        # Assert: User's custom mode is still present
        current_content = custom_modes_file.read_text()
        assert "slug: my-mode" in current_content, "User's custom mode was removed!"
        assert "My Custom Mode" in current_content

        # Assert: Evolve mode is added with sentinels
        file_assertions.assert_sentinel_block_exists(custom_modes_file, "evolve-lite")
        assert "slug: evolve-lite" in current_content

        # Assert: No duplicate user modes
        assert current_content.count("slug: my-mode") == 1

    def test_preserves_existing_mcp_servers(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install evolve full mode when user has existing MCP servers - they must be preserved."""
        # Setup: Create user's MCP config
        mcp_file = bob_fixtures.create_existing_mcp_config(temp_project_dir)
        original_data = json.loads(mcp_file.read_text())

        # Action: Install evolve in full mode
        install_runner.run("install", platform="bob", mode="full")

        # Assert: User's MCP server is still present
        file_assertions.assert_valid_json(mcp_file)
        file_assertions.assert_json_has_key(mcp_file, ["mcpServers", "my-server"], "User's MCP server was removed!")

        # Assert: Evolve MCP server is added
        file_assertions.assert_json_has_key(mcp_file, ["mcpServers", "evolve"])

        # Assert: User's server config is unchanged
        current_data = json.loads(mcp_file.read_text())
        assert current_data["mcpServers"]["my-server"] == original_data["mcpServers"]["my-server"]

    def test_preserves_existing_evolve_mcp_server_fields(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install evolve full mode when evolve already exists - custom fields on that entry must be preserved."""
        mcp_file = bob_fixtures.create_existing_mcp_config_with_evolve(temp_project_dir)

        install_runner.run("install", platform="bob", mode="full")

        file_assertions.assert_valid_json(mcp_file)
        current_data = json.loads(mcp_file.read_text())
        evolve_server = current_data["mcpServers"]["evolve"]
        expected_args = [
            "run",
            "-i",
            "--rm",
            "1lleatmyhat/evolve:latest-core",
        ]

        assert evolve_server["command"] == "docker"
        assert evolve_server["args"] == expected_args
        assert evolve_server["disabled"] is False
        assert evolve_server["env"] == {"EVOLVE_PROFILE": "local"}
        assert evolve_server["metadata"] == {"managedBy": "user"}

    def test_preserves_all_bob_content_together_lite(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install evolve lite mode when user has all types of Bob content - all must be preserved."""
        # Setup: Create all types of user content
        custom_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        custom_command = bob_fixtures.create_existing_command(temp_project_dir)
        custom_modes = bob_fixtures.create_existing_custom_modes(temp_project_dir)

        # Save original content
        skill_content = (custom_skill / "SKILL.md").read_text()
        command_content = custom_command.read_text()

        # Action: Install evolve lite mode
        install_runner.run("install", platform="bob", mode="lite")

        # Assert: ALL user content is preserved
        file_assertions.assert_file_unchanged(custom_skill / "SKILL.md", skill_content)
        file_assertions.assert_file_unchanged(custom_command, command_content)

        assert "slug: my-mode" in custom_modes.read_text()

        # Assert: Evolve lite content is added
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-learn")
        file_assertions.assert_sentinel_block_exists(custom_modes, "evolve-lite")

    def test_preserves_all_bob_content_together_full(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install evolve full mode when user has all types of Bob content - all must be preserved."""
        # Setup: Create all types of user content
        custom_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        custom_command = bob_fixtures.create_existing_command(temp_project_dir)
        custom_modes = bob_fixtures.create_existing_custom_modes(temp_project_dir)
        mcp_config = bob_fixtures.create_existing_mcp_config(temp_project_dir)

        # Save original content
        skill_content = (custom_skill / "SKILL.md").read_text()
        command_content = custom_command.read_text()
        mcp_data = json.loads(mcp_config.read_text())

        # Action: Install evolve full mode
        install_runner.run("install", platform="bob", mode="full")

        # Assert: ALL user content is preserved
        file_assertions.assert_file_unchanged(custom_skill / "SKILL.md", skill_content)
        file_assertions.assert_file_unchanged(custom_command, command_content)

        assert "slug: my-mode" in custom_modes.read_text()

        current_mcp = json.loads(mcp_config.read_text())
        assert current_mcp["mcpServers"]["my-server"] == mcp_data["mcpServers"]["my-server"]

        # Assert: Evolve full mode content is added (MCP and Evolve custom mode)
        file_assertions.assert_sentinel_block_exists(custom_modes, "Evolve")
        file_assertions.assert_json_has_key(mcp_config, ["mcpServers", "evolve"])


@pytest.mark.platform_integrations
class TestRooPreservation:
    """Test that Roo installation preserves existing user data."""

    def test_preserves_existing_skills(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Install evolve when user has existing Roo skills - they must be preserved."""
        # Setup: Create user's custom skill
        custom_skill = roo_fixtures.create_existing_skill(temp_project_dir)
        original_content = (custom_skill / "SKILL.md").read_text()

        # Action: Install evolve
        install_runner.run("install", platform="roo")

        # Assert: User's skill is untouched
        file_assertions.assert_dir_exists(custom_skill)
        file_assertions.assert_file_unchanged(custom_skill / "SKILL.md", original_content)

        # Assert: Evolve skills are added
        roo_dir = temp_project_dir / ".roo"
        file_assertions.assert_dir_exists(roo_dir / "skills" / "evolve-learn")
        file_assertions.assert_dir_exists(roo_dir / "skills" / "evolve-recall")

    def test_preserves_existing_roomodes_json(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Install evolve when user has existing .roomodes (JSON) - it must be preserved."""
        # Setup: Create user's .roomodes in JSON format
        roomodes_file = roo_fixtures.create_existing_roomodes_json(temp_project_dir)
        original_data = json.loads(roomodes_file.read_text())

        # Action: Install evolve
        install_runner.run("install", platform="roo")

        # Assert: File is still valid JSON
        file_assertions.assert_valid_json(roomodes_file)

        # Assert: User's mode is still present
        current_data = json.loads(roomodes_file.read_text())
        user_modes = [m for m in current_data["customModes"] if m["slug"] == "my-roo-mode"]
        assert len(user_modes) == 1, "User's custom mode was removed!"
        assert user_modes[0] == original_data["customModes"][0]

        # Assert: Evolve mode is added
        evolve_modes = [m for m in current_data["customModes"] if m["slug"] == "evolve-lite"]
        assert len(evolve_modes) == 1, f"Evolve mode not added. Found {len(evolve_modes)} evolve-lite entries"

    def test_preserves_existing_evolve_roomodes_fields(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Install evolve when evolve-lite already exists in JSON .roomodes - custom fields on that mode must be preserved."""
        roomodes_file = roo_fixtures.create_existing_roomodes_json_with_evolve(temp_project_dir)

        install_runner.run("install", platform="roo")

        file_assertions.assert_valid_json(roomodes_file)
        current_data = json.loads(roomodes_file.read_text())
        evolve_modes = [m for m in current_data["customModes"] if m["slug"] == "evolve-lite"]
        assert len(evolve_modes) == 1
        assert evolve_modes[0]["name"] == "Evolve Lite"
        assert evolve_modes[0]["metadata"] == {"accent": "teal"}
        assert evolve_modes[0]["shortcuts"] == ["recall-first"]

    def test_preserves_existing_roomodes_yaml(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Install evolve when user has existing .roomodes (YAML) - it must be preserved."""
        # Setup: Create user's .roomodes in YAML format
        roomodes_file = roo_fixtures.create_existing_roomodes_yaml(temp_project_dir)

        # Action: Install evolve
        install_runner.run("install", platform="roo")

        # Assert: User's mode is still present
        current_content = roomodes_file.read_text()
        assert "slug: my-roo-mode" in current_content, "User's custom mode was removed!"
        assert "My Roo Mode" in current_content

        # Assert: Evolve mode is added with sentinels
        file_assertions.assert_sentinel_block_exists(roomodes_file, "evolve-lite")
        assert "slug: evolve-lite" in current_content

        # Assert: No duplicate user modes
        assert current_content.count("slug: my-roo-mode") == 1

    def test_preserves_all_roo_content_together(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Install evolve when user has all types of Roo content - all must be preserved."""
        # Setup: Create all types of user content
        custom_skill = roo_fixtures.create_existing_skill(temp_project_dir)
        roomodes_file = roo_fixtures.create_existing_roomodes_json(temp_project_dir)

        # Save original content
        skill_content = (custom_skill / "SKILL.md").read_text()
        roomodes_data = json.loads(roomodes_file.read_text())

        # Action: Install evolve
        install_runner.run("install", platform="roo")

        # Assert: ALL user content is preserved
        file_assertions.assert_file_unchanged(custom_skill / "SKILL.md", skill_content)

        current_roomodes = json.loads(roomodes_file.read_text())
        user_modes = [m for m in current_roomodes["customModes"] if m["slug"] == "my-roo-mode"]
        assert user_modes[0] == roomodes_data["customModes"][0]

        # Assert: Evolve content is added
        roo_dir = temp_project_dir / ".roo"
        file_assertions.assert_dir_exists(roo_dir / "skills" / "evolve-learn")
        evolve_modes = [m for m in current_roomodes["customModes"] if m["slug"] == "evolve-lite"]
        assert len(evolve_modes) == 1, f"Expected 1 evolve-lite mode, found {len(evolve_modes)}"


@pytest.mark.platform_integrations
class TestCodexPreservation:
    """Test that Codex installation preserves existing user data."""

    def test_preserves_existing_marketplace_entries(self, temp_project_dir, install_runner, codex_fixtures, file_assertions):
        """Install evolve when user already has marketplace entries - they must be preserved."""
        codex_fixtures.create_existing_plugin(temp_project_dir)
        marketplace_file = codex_fixtures.create_existing_marketplace(temp_project_dir)
        original_data = json.loads(marketplace_file.read_text())

        install_runner.run("install", platform="codex")

        file_assertions.assert_valid_json(marketplace_file)
        current_data = json.loads(marketplace_file.read_text())

        custom_plugins = [entry for entry in current_data["plugins"] if entry["name"] == "my-codex-plugin"]
        assert len(custom_plugins) == 1, "User's existing plugin entry was removed or duplicated!"
        assert custom_plugins[0] == original_data["plugins"][0]

        evolve_plugins = [entry for entry in current_data["plugins"] if entry["name"] == "evolve-lite"]
        assert len(evolve_plugins) == 1, "Evolve plugin entry missing from marketplace.json"

    def test_preserves_existing_hooks_and_plugin_files(self, temp_project_dir, install_runner, codex_fixtures, file_assertions):
        """Install evolve when user already has hooks and plugins - they must be preserved."""
        custom_plugin = codex_fixtures.create_existing_plugin(temp_project_dir)
        plugin_json = custom_plugin / ".codex-plugin" / "plugin.json"
        original_plugin_content = plugin_json.read_text()
        hooks_file = codex_fixtures.create_existing_hooks(temp_project_dir)

        install_runner.run("install", platform="codex")

        file_assertions.assert_file_unchanged(plugin_json, original_plugin_content)

        current_hooks = json.loads(hooks_file.read_text())
        session_start_hooks = current_hooks["hooks"]["SessionStart"]
        assert len(session_start_hooks) == 1, "User's SessionStart hook was removed!"

        prompt_hooks = current_hooks["hooks"]["UserPromptSubmit"]
        custom_prompt_hooks = [
            hook
            for group in prompt_hooks
            for hook in group.get("hooks", [])
            if hook.get("command") == "python3 ~/.codex/hooks/custom_prompt_memory.py"
        ]
        assert len(custom_prompt_hooks) == 1, "User's UserPromptSubmit hook was removed!"

        evolve_hooks = [
            group
            for group in prompt_hooks
            if any(
                "plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py" in hook.get("command", "")
                for hook in group.get("hooks", [])
            )
        ]
        assert len(evolve_hooks) == 1, "Evolve UserPromptSubmit hook was not added!"
        assert evolve_hooks[0].get("matcher") == ""


@pytest.mark.platform_integrations
class TestMultiPlatformPreservation:
    """Test that installing multiple platforms preserves all user data."""

    def test_install_all_platforms_preserves_everything(
        self, temp_project_dir, install_runner, bob_fixtures, roo_fixtures, codex_fixtures, file_assertions
    ):
        """Install all platforms when user has content everywhere - all must be preserved."""
        # Setup: Create user content for both platforms
        bob_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        bob_command = bob_fixtures.create_existing_command(temp_project_dir)
        bob_modes = bob_fixtures.create_existing_custom_modes(temp_project_dir)

        roo_skill = roo_fixtures.create_existing_skill(temp_project_dir)
        roo_modes = roo_fixtures.create_existing_roomodes_json(temp_project_dir)
        codex_plugin = codex_fixtures.create_existing_plugin(temp_project_dir)
        codex_marketplace = codex_fixtures.create_existing_marketplace(temp_project_dir)
        codex_hooks = codex_fixtures.create_existing_hooks(temp_project_dir)

        # Save original content
        bob_skill_content = (bob_skill / "SKILL.md").read_text()
        bob_command_content = bob_command.read_text()
        roo_skill_content = (roo_skill / "SKILL.md").read_text()
        codex_plugin_content = (codex_plugin / ".codex-plugin" / "plugin.json").read_text()
        codex_marketplace_data = json.loads(codex_marketplace.read_text())

        # Action: Install all platforms
        install_runner.run("install", platform="all")

        # Assert: ALL Bob content is preserved
        file_assertions.assert_file_unchanged(bob_skill / "SKILL.md", bob_skill_content)
        file_assertions.assert_file_unchanged(bob_command, bob_command_content)
        assert "slug: my-mode" in bob_modes.read_text()

        # Assert: ALL Roo content is preserved
        file_assertions.assert_file_unchanged(roo_skill / "SKILL.md", roo_skill_content)
        roo_data = json.loads(roo_modes.read_text())
        assert any(m["slug"] == "my-roo-mode" for m in roo_data["customModes"])

        # Assert: ALL Codex content is preserved
        file_assertions.assert_file_unchanged(codex_plugin / ".codex-plugin" / "plugin.json", codex_plugin_content)
        current_marketplace = json.loads(codex_marketplace.read_text())
        assert any(entry["name"] == "my-codex-plugin" for entry in current_marketplace["plugins"])
        assert codex_marketplace_data["plugins"][0] in current_marketplace["plugins"]
        current_hooks = json.loads(codex_hooks.read_text())
        assert any(
            hook.get("command") == "python3 ~/.codex/hooks/custom_prompt_memory.py"
            for group in current_hooks["hooks"]["UserPromptSubmit"]
            for hook in group.get("hooks", [])
        )

        # Assert: Evolve content is added everywhere
        file_assertions.assert_dir_exists(temp_project_dir / ".bob" / "skills" / "evolve-learn")
        file_assertions.assert_dir_exists(temp_project_dir / ".roo" / "skills" / "evolve-learn")
        file_assertions.assert_dir_exists(temp_project_dir / "plugins" / "evolve-lite")
