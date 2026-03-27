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

        # Assert: Skills still exist
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-learn")
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-recall")

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

        shutil.rmtree(bob_dir / "skills" / "evolve-learn")

        # Reinstall
        install_runner.run("install", platform="bob")

        # Assert: Deleted skill is restored
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-learn")
        file_assertions.assert_file_exists(bob_dir / "skills" / "evolve-learn" / "SKILL.md")

        # Assert: Other components still intact
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-recall")
        file_assertions.assert_file_exists(bob_dir / "custom_modes.yaml")


@pytest.mark.platform_integrations
class TestRooIdempotency:
    """Test that Roo installation is idempotent."""

    def test_multiple_installs_json_roomodes(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Running install twice with JSON .roomodes should be safe."""
        # Create initial JSON .roomodes
        roo_fixtures.create_existing_roomodes_json(temp_project_dir)

        # First install
        install_runner.run("install", platform="roo")

        roomodes_file = temp_project_dir / ".roomodes"
        first_content = roomodes_file.read_text()
        first_data = json.loads(first_content)

        # Second install
        install_runner.run("install", platform="roo")

        # Assert: Data is identical (still JSON, content unchanged)
        second_content = roomodes_file.read_text()
        second_data = json.loads(second_content)
        assert first_data == second_data, ".roomodes changed after second install"

        # Assert: Only one evolve-lite entry
        evolve_modes = [m for m in second_data["customModes"] if m["slug"] == "evolve-lite"]
        assert len(evolve_modes) == 1, "Duplicate evolve-lite entries found"

    def test_multiple_installs_yaml_roomodes(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Running install twice with YAML .roomodes should be safe."""
        # Create initial YAML .roomodes
        roo_fixtures.create_existing_roomodes_yaml(temp_project_dir)

        # First install
        install_runner.run("install", platform="roo")

        roomodes_file = temp_project_dir / ".roomodes"
        first_content = roomodes_file.read_text()

        # Second install
        install_runner.run("install", platform="roo")

        # Assert: Content is identical
        second_content = roomodes_file.read_text()
        assert first_content == second_content, ".roomodes changed after second install"

        # Assert: Only one sentinel block
        assert first_content.count("# >>>evolve:evolve-lite<<<") == 1
        assert first_content.count("# <<<evolve:evolve-lite<<<") == 1

    def test_install_creates_yaml_when_no_roomodes(self, temp_project_dir, install_runner, file_assertions):
        """When .roomodes doesn't exist, install creates it as YAML (roo-code preferred format)."""
        # First install (no .roomodes exists)
        install_runner.run("install", platform="roo")

        roomodes_file = temp_project_dir / ".roomodes"

        # Assert: File created as YAML
        file_assertions.assert_file_exists(roomodes_file)
        content = roomodes_file.read_text()

        # Verify it's YAML format (contains YAML markers, not JSON)
        assert "customModes:" in content, "Missing YAML customModes key"
        assert "slug: evolve-lite" in content, "Missing evolve-lite mode"

        # Second install should be idempotent
        first_content = content
        install_runner.run("install", platform="roo")

        second_content = roomodes_file.read_text()
        assert first_content == second_content, ".roomodes changed after second install"


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
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-learn")

        # Uninstall
        install_runner.run("uninstall", platform="bob")

        file_assertions.assert_dir_not_exists(bob_dir / "skills" / "evolve-learn")
        file_assertions.assert_dir_not_exists(bob_dir / "skills" / "evolve-recall")

        # Reinstall
        install_runner.run("install", platform="bob")

        # Assert: Evolve content is back
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-learn")
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-recall")
        file_assertions.assert_sentinel_block_exists(bob_dir / "custom_modes.yaml", "evolve-lite")

        # Assert: User content still intact
        file_assertions.assert_dir_exists(bob_dir / "skills" / "my-custom-skill")
        custom_modes = (bob_dir / "custom_modes.yaml").read_text()
        assert "slug: my-mode" in custom_modes

    def test_roo_uninstall_install_cycle(self, temp_project_dir, install_runner, roo_fixtures, file_assertions):
        """Uninstalling and reinstalling Roo should work correctly."""
        # Create user content
        roo_fixtures.create_existing_skill(temp_project_dir)
        roo_fixtures.create_existing_roomodes_json(temp_project_dir)

        # Install
        install_runner.run("install", platform="roo")

        roo_dir = temp_project_dir / ".roo"
        file_assertions.assert_dir_exists(roo_dir / "skills" / "evolve-learn")

        # Uninstall
        install_runner.run("uninstall", platform="roo")

        file_assertions.assert_dir_not_exists(roo_dir / "skills" / "evolve-learn")
        file_assertions.assert_dir_not_exists(roo_dir / "skills" / "evolve-recall")

        # Reinstall
        install_runner.run("install", platform="roo")

        # Assert: Evolve content is back
        file_assertions.assert_dir_exists(roo_dir / "skills" / "evolve-learn")
        file_assertions.assert_dir_exists(roo_dir / "skills" / "evolve-recall")

        # Assert: evolve-lite mode is present (file is still JSON from initial setup)
        roomodes_file = temp_project_dir / ".roomodes"
        data = json.loads(roomodes_file.read_text())
        assert any(m["slug"] == "evolve-lite" for m in data["customModes"]), "evolve-lite mode missing after reinstall"

        # Assert: User content still intact
        file_assertions.assert_dir_exists(roo_dir / "skills" / "my-roo-skill")
        assert any(m["slug"] == "my-roo-mode" for m in data["customModes"])
