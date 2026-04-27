"""
Tests to verify that skill directory names match the expected naming convention.

This test catches issues where skill directories are named incorrectly,
which causes installation failures when install.sh tries to copy them.
"""

import pytest


@pytest.mark.platform_integrations
class TestSkillDirectoryNames:
    """Test that skill directories follow the correct naming convention."""

    def test_bob_lite_skill_directories_exist(self, platform_integrations_dir):
        """Verify that all Bob lite skills referenced in install.sh actually exist."""
        bob_lite_skills = platform_integrations_dir / "bob" / "evolve-lite" / "skills"

        # These are the skills that install.sh tries to copy
        expected_skills = [
            "evolve-lite:learn",
            "evolve-lite:recall",
            "evolve-lite:publish",
            "evolve-lite:subscribe",
            "evolve-lite:unsubscribe",
            "evolve-lite:sync",
        ]

        for skill_name in expected_skills:
            skill_dir = bob_lite_skills / skill_name
            assert skill_dir.is_dir(), (
                f"Skill directory not found: {skill_dir}\n"
                f"install.sh references this skill but it doesn't exist.\n"
                f"This will cause installation failures."
            )

            # Verify SKILL.md exists
            skill_md = skill_dir / "SKILL.md"
            assert skill_md.is_file(), f"SKILL.md not found in {skill_dir}\nEvery skill must have a SKILL.md file."

    def test_bob_lite_skills_follow_naming_convention(self, platform_integrations_dir):
        """Verify that Bob lite skills follow the 'evolve-lite:*' naming convention."""
        bob_lite_skills = platform_integrations_dir / "bob" / "evolve-lite" / "skills"

        if not bob_lite_skills.exists():
            pytest.skip("Bob lite skills directory doesn't exist")

        for skill_dir in bob_lite_skills.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_name = skill_dir.name

            # All evolve skills should start with "evolve-lite:"
            assert skill_name.startswith("evolve-lite:"), (
                f"Skill directory '{skill_name}' doesn't follow naming convention.\n"
                f"Expected: 'evolve-lite:<skill-name>'\n"
                f"Got: '{skill_name}'\n"
                f"This will cause installation failures because install.sh expects the 'evolve-lite:' prefix."
            )

    def test_bob_lite_command_files_exist(self, platform_integrations_dir):
        """Verify that command files exist for all Bob lite skills."""
        bob_lite_commands = platform_integrations_dir / "bob" / "evolve-lite" / "commands"
        bob_lite_skills = platform_integrations_dir / "bob" / "evolve-lite" / "skills"

        # Get all skill names
        if not bob_lite_skills.exists():
            pytest.skip("Bob lite skills directory doesn't exist")

        skill_names = [d.name for d in bob_lite_skills.iterdir() if d.is_dir() and d.name.startswith("evolve-lite:")]

        # Verify each skill has a corresponding command file
        for skill_name in skill_names:
            command_file = bob_lite_commands / f"{skill_name}.md"
            assert command_file.is_file(), (
                f"Command file not found: {command_file}\n"
                f"Skill '{skill_name}' exists but has no corresponding command file.\n"
                f"Every skill should have a command file in the commands/ directory."
            )

    def test_bob_lite_install_script_uses_dynamic_copying(self, install_script):
        """Verify that install.sh uses dynamic skill copying (not hardcoded skill names)."""
        install_content = install_script.read_text()

        # Verify the script uses iterdir() to copy all skills dynamically
        assert "for skill_dir in sorted(skills_src.iterdir())" in install_content, (
            "install.sh should use dynamic skill copying with iterdir() to automatically install all skills in the skills directory"
        )

    def test_bob_lite_installation_succeeds(self, temp_project_dir, install_runner, file_assertions):
        """Integration test: Verify Bob lite installation completes without errors."""
        # This test will fail if any skill directories are missing or misnamed
        result = install_runner.run("install", platform="bob", mode="lite")

        # Verify installation succeeded
        assert result.returncode == 0, f"Bob lite installation failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        # Verify all expected skills were installed
        bob_dir = temp_project_dir / ".bob"
        expected_skills = [
            "evolve-lite:learn",
            "evolve-lite:recall",
            "evolve-lite:publish",
            "evolve-lite:subscribe",
            "evolve-lite:unsubscribe",
            "evolve-lite:sync",
        ]

        for skill_name in expected_skills:
            skill_dir = bob_dir / "skills" / skill_name
            file_assertions.assert_dir_exists(skill_dir, f"Skill '{skill_name}' was not installed")


# Made with Bob
