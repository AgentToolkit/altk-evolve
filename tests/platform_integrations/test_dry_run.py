"""
Tests that --dry-run never writes files, exits 0, and prints expected banners.

Two scenarios are covered:
  TestDryRunLocal  — script runs from a dir that has a platform-integrations/ sibling
                     (normal dev / CI context)
  TestDryRunRemote — script copied to an isolated dir with no local source tree,
                     simulating curl | bash
"""

import pytest


@pytest.mark.platform_integrations
class TestDryRunLocal:
    """Dry-run tests using the in-repo install.sh (local source tree present)."""

    def test_all_platforms_dry_run_creates_no_files(self, temp_project_dir, install_runner):
        """Dry-running all platforms must not create any files."""
        result = install_runner.run("install", platform="all", dry_run=True)

        assert result.returncode == 0
        assert "DRY RUN" in result.stdout
        assert not (temp_project_dir / ".bob").exists()
        assert not (temp_project_dir / "plugins").exists()
        assert not (temp_project_dir / ".agents").exists()
        assert not (temp_project_dir / ".codex").exists()

    def test_bob_dry_run_mentions_expected_operations(self, temp_project_dir, install_runner, platform_integrations_dir):
        """Bob dry-run output should name the skills it would copy."""
        result = install_runner.run("install", platform="bob", mode="lite", dry_run=True)

        assert result.returncode == 0
        assert "DRY RUN" in result.stdout
        skills_src = platform_integrations_dir / "bob" / "evolve-lite" / "skills"
        for skill_dir in skills_src.iterdir():
            if skill_dir.is_dir():
                assert skill_dir.name in result.stdout, f"Expected skill '{skill_dir.name}' to appear in dry-run output"
        assert "custom_modes.yaml" in result.stdout
        assert not (temp_project_dir / ".bob").exists()

    def test_codex_dry_run_creates_no_files(self, temp_project_dir, install_runner):
        """Codex dry-run must not write the plugin tree, marketplace entry, or hook."""
        result = install_runner.run("install", platform="codex", dry_run=True)

        assert result.returncode == 0
        assert "DRY RUN" in result.stdout
        assert not (temp_project_dir / "plugins").exists()
        assert not (temp_project_dir / ".agents" / "plugins" / "marketplace.json").exists()
        assert not (temp_project_dir / ".codex" / "hooks.json").exists()

    def test_claude_dry_run_creates_no_files(self, temp_project_dir, install_runner):
        """Claude dry-run must not invoke the real CLI or leave any files."""
        result = install_runner.run("install", platform="claude", dry_run=True)

        assert result.returncode == 0
        assert "DRY RUN" in result.stdout

    def test_uninstall_dry_run_creates_no_changes(self, temp_project_dir, install_runner):
        """Dry-running uninstall on a clean dir should exit 0 and touch nothing."""
        result = install_runner.run("uninstall", platform="bob", dry_run=True)

        assert result.returncode == 0
        assert "DRY RUN" in result.stdout
        assert not (temp_project_dir / ".bob").exists()


@pytest.mark.platform_integrations
class TestDryRunRemote:
    """
    Dry-run tests using an isolated install.sh copy that has no local source tree.

    This is the critical scenario: curl | bash --dry-run must never fail just
    because the source directory does not exist on the machine.
    """

    def test_all_platforms_dry_run_exits_zero(self, temp_project_dir, remote_install_runner):
        """Remote dry-run of all platforms must succeed even with no local source."""
        result = remote_install_runner.run("install", platform="all", dry_run=True)

        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert "DRY RUN" in result.stdout

    def test_all_platforms_dry_run_creates_no_files(self, temp_project_dir, remote_install_runner):
        """Remote dry-run must not create any files in the project dir."""
        remote_install_runner.run("install", platform="all", dry_run=True)

        assert not (temp_project_dir / ".bob").exists()
        assert not (temp_project_dir / "plugins").exists()
        assert not (temp_project_dir / ".agents").exists()
        assert not (temp_project_dir / ".codex").exists()

    def test_claude_only_remote_dry_run(self, temp_project_dir, remote_install_runner):
        """Claude-only remote dry-run should succeed (Claude needs no source tree)."""
        result = remote_install_runner.run("install", platform="claude", dry_run=True)

        assert result.returncode == 0
        assert "DRY RUN" in result.stdout
