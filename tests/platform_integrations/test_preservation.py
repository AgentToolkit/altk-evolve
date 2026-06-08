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

        # Assert: All evolve skills from the source tree are installed
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_all_bob_skills_installed(bob_dir)

    def test_preserves_existing_commands(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install evolve when user has existing commands - they must be preserved."""
        # Setup: Create user's custom command
        custom_command = bob_fixtures.create_existing_command(temp_project_dir)
        original_content = custom_command.read_text()

        # Action: Install evolve
        install_runner.run("install", platform="bob")

        # Assert: User's command is untouched
        file_assertions.assert_file_unchanged(custom_command, original_content)

        # Assert: All evolve commands from the source tree are installed
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_all_bob_commands_installed(bob_dir)

    def test_preserves_existing_custom_modes_and_user_rules(
        self, temp_project_dir, install_runner, bob_fixtures, file_assertions, bob_rules_file
    ):
        """Lite install must leave the user's custom_modes.yaml and unrelated rules untouched.

        Lite no longer merges a mode into custom_modes.yaml, nor injects an
        AGENTS.md import. The evolve always-on instructions live in Bob's GLOBAL
        rules dir at ~/.bob/rules/00-evolve-lite.md. A pre-existing, unrelated
        rules file (e.g. ~/.bob/rules/99-user.md) must be left intact, and no
        AGENTS.md must be created.
        """
        # Setup: user's custom mode, plus a pre-existing unrelated global rules file.
        custom_modes_file = bob_fixtures.create_existing_custom_modes(temp_project_dir)
        original_modes_content = custom_modes_file.read_text()

        user_rule = bob_rules_file.parent / "99-user.md"
        user_rule.parent.mkdir(parents=True, exist_ok=True)
        original_rule_content = "# My personal rules\n\nAlways prefer tabs.\n"
        user_rule.write_text(original_rule_content)

        # Action: Install evolve (lite is the default mode)
        install_runner.run("install", platform="bob")

        # Assert: User's custom_modes.yaml is byte-for-byte unchanged.
        file_assertions.assert_file_unchanged(custom_modes_file, original_modes_content)

        # Assert: User's unrelated rules file is byte-for-byte unchanged.
        file_assertions.assert_file_unchanged(user_rule, original_rule_content)

        # Assert: The evolve instructions live in the global rules file, holding
        # the full EVOLVE.md text; no AGENTS.md was created.
        file_assertions.assert_file_exists(bob_rules_file)
        assert "self-directed memory" in bob_rules_file.read_text()
        file_assertions.assert_file_not_exists(temp_project_dir / "AGENTS.md")
        file_assertions.assert_file_not_exists((temp_project_dir / ".bob") / "AGENTS.md")

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

    def test_refreshes_managed_evolve_mcp_server_fields_and_preserves_custom_fields(
        self, temp_project_dir, install_runner, bob_fixtures, file_assertions
    ):
        """Install evolve full mode when evolve exists - managed fields should refresh while custom fields are preserved."""
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

    def test_preserves_all_bob_content_together_lite(self, temp_project_dir, install_runner, bob_fixtures, file_assertions, bob_rules_file):
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

        # User's custom_modes.yaml is untouched by lite (it no longer merges modes).
        assert "slug: my-mode" in custom_modes.read_text()

        # Assert: Evolve lite content is added. Skills/commands/lib are copied, and the
        # always-on instructions are wired via the GLOBAL rules file (not custom_modes.yaml).
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_all_bob_skills_installed(bob_dir)
        file_assertions.assert_all_bob_commands_installed(bob_dir)
        file_assertions.assert_dir_exists(bob_dir / "lib" / "evolve-lite")
        file_assertions.assert_file_exists(bob_rules_file)
        # No AGENTS.md or per-project EVOLVE.md copy is created.
        file_assertions.assert_file_not_exists(temp_project_dir / "AGENTS.md")
        file_assertions.assert_file_not_exists(bob_dir / "EVOLVE.md")

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

    def test_preserves_existing_hooks_and_plugin_files(
        self, temp_project_dir, install_runner, codex_fixtures, file_assertions, codex_agents_file
    ):
        """Install evolve when user already has hooks and plugins - they must be preserved.

        Codex no longer registers any hooks; it drops a COPY of EVOLVE.md on disk
        and injects a SINGLE managed pointer line into the (sandboxed)
        ~/.codex/AGENTS.md. So the user's hooks.json must be left COMPLETELY
        UNCHANGED (no Evolve sync/recall hook added), and the pointer line must
        appear in AGENTS.md instead.
        """
        custom_plugin = codex_fixtures.create_existing_plugin(temp_project_dir)
        plugin_json = custom_plugin / ".codex-plugin" / "plugin.json"
        original_plugin_content = plugin_json.read_text()
        hooks_file = codex_fixtures.create_existing_hooks(temp_project_dir)
        original_hooks_content = hooks_file.read_text()

        install_runner.run("install", platform="codex")

        # The user's plugin.json is untouched.
        file_assertions.assert_file_unchanged(plugin_json, original_plugin_content)

        # The user's hooks.json is byte-for-byte unchanged: no Evolve hook is added.
        file_assertions.assert_file_unchanged(hooks_file, original_hooks_content)

        current_hooks = json.loads(hooks_file.read_text())
        # SessionStart count stays at the user's original (1) — no sync hook added.
        session_start_hooks = current_hooks["hooks"]["SessionStart"]
        assert len(session_start_hooks) == 1, "Codex install must not add a SessionStart hook anymore."
        assert any(
            any(hook.get("command") == "python3 ~/.codex/hooks/session_start.py" for hook in group.get("hooks", []))
            for group in session_start_hooks
        ), "User's SessionStart hook was removed!"
        assert all(
            "plugins/evolve-lite/skills/evolve-lite/sync/scripts/sync.py" not in hook.get("command", "")
            for group in session_start_hooks
            for hook in group.get("hooks", [])
        ), "Codex install must no longer add an Evolve SessionStart hook."

        prompt_hooks = current_hooks["hooks"]["UserPromptSubmit"]
        custom_prompt_hooks = [
            hook
            for group in prompt_hooks
            for hook in group.get("hooks", [])
            if hook.get("command") == "python3 ~/.codex/hooks/custom_prompt_memory.py"
        ]
        assert len(custom_prompt_hooks) == 1, "User's UserPromptSubmit hook was removed!"
        assert all(
            "plugins/evolve-lite/skills/evolve-lite/recall/scripts/retrieve_entities.py" not in hook.get("command", "")
            for group in prompt_hooks
            for hook in group.get("hooks", [])
        ), "Codex install must no longer add an Evolve UserPromptSubmit hook."

        # The evolve always-on instructions now live behind a single managed
        # pointer line in ~/.codex/AGENTS.md (sandboxed).
        MANAGED_MARKER = "<!-- evolve-lite:managed -->"
        marker_lines = [ln for ln in codex_agents_file.read_text().splitlines() if MANAGED_MARKER in ln]
        assert len(marker_lines) == 1, f"Expected exactly one managed line, got {marker_lines!r}"


@pytest.mark.platform_integrations
class TestMultiPlatformPreservation:
    """Test that installing multiple platforms preserves all user data."""

    def test_install_all_platforms_preserves_everything(
        self, temp_project_dir, install_runner, bob_fixtures, codex_fixtures, file_assertions
    ):
        """Install all platforms when user has content everywhere - all must be preserved."""
        # Setup: Create user content for both platforms
        bob_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        bob_command = bob_fixtures.create_existing_command(temp_project_dir)
        bob_modes = bob_fixtures.create_existing_custom_modes(temp_project_dir)

        codex_plugin = codex_fixtures.create_existing_plugin(temp_project_dir)
        codex_marketplace = codex_fixtures.create_existing_marketplace(temp_project_dir)
        codex_hooks = codex_fixtures.create_existing_hooks(temp_project_dir)

        # Save original content
        bob_skill_content = (bob_skill / "SKILL.md").read_text()
        bob_command_content = bob_command.read_text()
        codex_plugin_content = (codex_plugin / ".codex-plugin" / "plugin.json").read_text()
        codex_marketplace_data = json.loads(codex_marketplace.read_text())

        # Action: Install all platforms
        install_runner.run("install", platform="all")

        # Assert: ALL Bob content is preserved
        file_assertions.assert_file_unchanged(bob_skill / "SKILL.md", bob_skill_content)
        file_assertions.assert_file_unchanged(bob_command, bob_command_content)
        assert "slug: my-mode" in bob_modes.read_text()

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
        file_assertions.assert_dir_exists(temp_project_dir / ".bob" / "skills" / "evolve-lite-learn")
        file_assertions.assert_dir_exists(temp_project_dir / "plugins" / "evolve-lite")
