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
        """Install kaizen when user has existing custom skills - they must be preserved."""
        # Setup: Create user's custom skill
        custom_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        original_content = (custom_skill / "SKILL.md").read_text()

        # Action: Install kaizen
        install_runner.run("install", platform="bob")

        # Assert: User's skill is untouched
        file_assertions.assert_dir_exists(custom_skill)
        file_assertions.assert_file_unchanged(custom_skill / "SKILL.md", original_content)

        # Assert: Kaizen skills are added
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_dir_exists(bob_dir / "skills" / "kaizen-learn")
        file_assertions.assert_dir_exists(bob_dir / "skills" / "kaizen-recall")

    def test_preserves_existing_commands(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install kaizen when user has existing commands - they must be preserved."""
        # Setup: Create user's custom command
        custom_command = bob_fixtures.create_existing_command(temp_project_dir)
        original_content = custom_command.read_text()

        # Action: Install kaizen
        install_runner.run("install", platform="bob")

        # Assert: User's command is untouched
        file_assertions.assert_file_unchanged(custom_command, original_content)

        # Assert: Kaizen commands are added
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_file_exists(bob_dir / "commands" / "kaizen:learn.md")
        file_assertions.assert_file_exists(bob_dir / "commands" / "kaizen:recall.md")

    def test_preserves_existing_custom_modes_yaml(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install kaizen when user has existing custom modes - they must be preserved."""
        # Setup: Create user's custom mode
        custom_modes_file = bob_fixtures.create_existing_custom_modes(temp_project_dir)

        # Action: Install kaizen
        install_runner.run("install", platform="bob")

        # Assert: User's custom mode is still present
        current_content = custom_modes_file.read_text()
        assert "slug: my-mode" in current_content, "User's custom mode was removed!"
        assert "My Custom Mode" in current_content

        # Assert: Kaizen mode is added with sentinels
        file_assertions.assert_sentinel_block_exists(custom_modes_file, "kaizen-lite")
        assert "slug: kaizen-lite" in current_content

        # Assert: No duplicate user modes
        assert current_content.count("slug: my-mode") == 1

    def test_preserves_existing_mcp_servers(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install kaizen full mode when user has existing MCP servers - they must be preserved."""
        # Setup: Create user's MCP config
        mcp_file = bob_fixtures.create_existing_mcp_config(temp_project_dir)
        original_data = json.loads(mcp_file.read_text())

        # Action: Install kaizen in full mode
        install_runner.run("install", platform="bob", mode="full")

        # Assert: User's MCP server is still present
        file_assertions.assert_valid_json(mcp_file)
        file_assertions.assert_json_has_key(mcp_file, ["mcpServers", "my-server"], "User's MCP server was removed!")

        # Assert: Kaizen MCP server is added
        file_assertions.assert_json_has_key(mcp_file, ["mcpServers", "kaizen"])

        # Assert: User's server config is unchanged
        current_data = json.loads(mcp_file.read_text())
        assert current_data["mcpServers"]["my-server"] == original_data["mcpServers"]["my-server"]

    def test_preserves_all_bob_content_together(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """Install kaizen when user has all types of Bob content - all must be preserved."""
        # Setup: Create all types of user content
        custom_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        custom_command = bob_fixtures.create_existing_command(temp_project_dir)
        custom_modes = bob_fixtures.create_existing_custom_modes(temp_project_dir)
        mcp_config = bob_fixtures.create_existing_mcp_config(temp_project_dir)

        # Save original content
        skill_content = (custom_skill / "SKILL.md").read_text()
        command_content = custom_command.read_text()
        mcp_data = json.loads(mcp_config.read_text())

        # Action: Install kaizen full mode
        install_runner.run("install", platform="bob", mode="full")

        # Assert: ALL user content is preserved
        file_assertions.assert_file_unchanged(custom_skill / "SKILL.md", skill_content)
        file_assertions.assert_file_unchanged(custom_command, command_content)

        assert "slug: my-mode" in custom_modes.read_text()

        current_mcp = json.loads(mcp_config.read_text())
        assert current_mcp["mcpServers"]["my-server"] == mcp_data["mcpServers"]["my-server"]

        # Assert: Kaizen content is added
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_dir_exists(bob_dir / "skills" / "kaizen-learn")
        file_assertions.assert_sentinel_block_exists(custom_modes, "kaizen-lite")
        file_assertions.assert_json_has_key(mcp_config, ["mcpServers", "kaizen"])


@pytest.mark.platform_integrations
class TestRooPreservation:
    """Test that Roo installation preserves existing user data."""

    def test_preserves_existing_skills(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Install kaizen when user has existing Roo skills - they must be preserved."""
        # Setup: Create user's custom skill
        custom_skill = roo_fixtures.create_existing_skill(temp_project_dir)
        original_content = (custom_skill / "SKILL.md").read_text()

        # Action: Install kaizen
        install_runner.run("install", platform="roo")

        # Assert: User's skill is untouched
        file_assertions.assert_dir_exists(custom_skill)
        file_assertions.assert_file_unchanged(custom_skill / "SKILL.md", original_content)

        # Assert: Kaizen skills are added
        roo_dir = temp_project_dir / ".roo"
        file_assertions.assert_dir_exists(roo_dir / "skills" / "kaizen-learn")
        file_assertions.assert_dir_exists(roo_dir / "skills" / "kaizen-recall")

    def test_preserves_existing_roomodes_json(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Install kaizen when user has existing .roomodes (JSON) - it must be preserved."""
        # Setup: Create user's .roomodes in JSON format
        roomodes_file = roo_fixtures.create_existing_roomodes_json(temp_project_dir)
        original_data = json.loads(roomodes_file.read_text())

        # Action: Install kaizen
        install_runner.run("install", platform="roo")

        # Assert: File is still valid JSON
        file_assertions.assert_valid_json(roomodes_file)

        # Assert: User's mode is still present
        current_data = json.loads(roomodes_file.read_text())
        user_modes = [m for m in current_data["customModes"] if m["slug"] == "my-roo-mode"]
        assert len(user_modes) == 1, "User's custom mode was removed!"
        assert user_modes[0] == original_data["customModes"][0]

        # Assert: Kaizen mode is added
        kaizen_modes = [m for m in current_data["customModes"] if m["slug"] == "kaizen-lite"]
        assert len(kaizen_modes) == 1, f"Kaizen mode not added. Found {len(kaizen_modes)} kaizen-lite entries"

    def test_preserves_existing_roomodes_yaml(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Install kaizen when user has existing .roomodes (YAML) - it must be preserved."""
        # Setup: Create user's .roomodes in YAML format
        roomodes_file = roo_fixtures.create_existing_roomodes_yaml(temp_project_dir)

        # Action: Install kaizen
        install_runner.run("install", platform="roo")

        # Assert: User's mode is still present
        current_content = roomodes_file.read_text()
        assert "slug: my-roo-mode" in current_content, "User's custom mode was removed!"
        assert "My Roo Mode" in current_content

        # Assert: Kaizen mode is added with sentinels
        file_assertions.assert_sentinel_block_exists(roomodes_file, "kaizen-lite")
        assert "slug: kaizen-lite" in current_content

        # Assert: No duplicate user modes
        assert current_content.count("slug: my-roo-mode") == 1

    def test_preserves_all_roo_content_together(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Install kaizen when user has all types of Roo content - all must be preserved."""
        # Setup: Create all types of user content
        custom_skill = roo_fixtures.create_existing_skill(temp_project_dir)
        roomodes_file = roo_fixtures.create_existing_roomodes_json(temp_project_dir)

        # Save original content
        skill_content = (custom_skill / "SKILL.md").read_text()
        roomodes_data = json.loads(roomodes_file.read_text())

        # Action: Install kaizen
        install_runner.run("install", platform="roo")

        # Assert: ALL user content is preserved
        file_assertions.assert_file_unchanged(custom_skill / "SKILL.md", skill_content)

        current_roomodes = json.loads(roomodes_file.read_text())
        user_modes = [m for m in current_roomodes["customModes"] if m["slug"] == "my-roo-mode"]
        assert user_modes[0] == roomodes_data["customModes"][0]

        # Assert: Kaizen content is added
        roo_dir = temp_project_dir / ".roo"
        file_assertions.assert_dir_exists(roo_dir / "skills" / "kaizen-learn")
        kaizen_modes = [m for m in current_roomodes["customModes"] if m["slug"] == "kaizen-lite"]
        assert len(kaizen_modes) == 1, f"Expected 1 kaizen-lite mode, found {len(kaizen_modes)}"


@pytest.mark.platform_integrations
class TestMultiPlatformPreservation:
    """Test that installing multiple platforms preserves all user data."""

    def test_install_all_platforms_preserves_everything(
        self, temp_project_dir, install_runner, bob_fixtures, roo_fixtures, file_assertions
    ):
        """Install all platforms when user has content everywhere - all must be preserved."""
        # Setup: Create user content for both platforms
        bob_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        bob_command = bob_fixtures.create_existing_command(temp_project_dir)
        bob_modes = bob_fixtures.create_existing_custom_modes(temp_project_dir)

        roo_skill = roo_fixtures.create_existing_skill(temp_project_dir)
        roo_modes = roo_fixtures.create_existing_roomodes_json(temp_project_dir)

        # Save original content
        bob_skill_content = (bob_skill / "SKILL.md").read_text()
        bob_command_content = bob_command.read_text()
        roo_skill_content = (roo_skill / "SKILL.md").read_text()

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

        # Assert: Kaizen content is added to both
        file_assertions.assert_dir_exists(temp_project_dir / ".bob" / "skills" / "kaizen-learn")
        file_assertions.assert_dir_exists(temp_project_dir / ".roo" / "skills" / "kaizen-learn")
