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

    def test_bob_lite_install_script_references_match_actual_directories(self, platform_integrations_dir, install_script):
        """Verify that skill names in install.sh match actual directory names."""
        # Read install.sh and extract skill directory references
        install_content = install_script.read_text()

        # Find the lines that copy skills
        # Looking for patterns like: copy_tree(bob_source_lite / "skills" / "evolve-lite:learn", ...)
        import re

        pattern = r'bob_source_lite / "skills" / "([^"]+)"'
        referenced_skills = re.findall(pattern, install_content)

        assert referenced_skills, "Could not find skill references in install.sh"

        # Verify each referenced skill exists
        bob_lite_skills = platform_integrations_dir / "bob" / "evolve-lite" / "skills"

        for skill_name in referenced_skills:
            skill_dir = bob_lite_skills / skill_name
            assert skill_dir.is_dir(), (
                f"install.sh references skill '{skill_name}' but directory doesn't exist: {skill_dir}\n"
                f"This will cause 'Source directory not found' errors during installation."
            )

    def test_bob_lite_no_orphaned_skill_directories(self, platform_integrations_dir, install_script):
        """Verify there are no skill directories that aren't referenced in install.sh."""
        bob_lite_skills = platform_integrations_dir / "bob" / "evolve-lite" / "skills"

        if not bob_lite_skills.exists():
            pytest.skip("Bob lite skills directory doesn't exist")

        # Get actual skill directories
        actual_skills = {d.name for d in bob_lite_skills.iterdir() if d.is_dir()}

        # Get referenced skills from install.sh
        install_content = install_script.read_text()
        import re

        pattern = r'bob_source_lite / "skills" / "([^"]+)"'
        referenced_skills = set(re.findall(pattern, install_content))

        # Find orphaned directories (exist but not referenced)
        orphaned = actual_skills - referenced_skills

        assert not orphaned, (
            f"Found skill directories that aren't referenced in install.sh: {orphaned}\n"
            f"These skills won't be installed. Either:\n"
            f"1. Add them to install.sh if they should be installed, or\n"
            f"2. Remove them if they're obsolete"
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
