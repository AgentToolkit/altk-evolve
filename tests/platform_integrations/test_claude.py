"""
Tests for the Claude platform integration installer behavior.

Claude install delegates entirely to the claude CLI via the marketplace workflow.
These tests control PATH to simulate the CLI being absent, which lets us verify
fallback output without needing the actual CLI installed.
"""

import os

import pytest


# PATH that contains no claude binary — forces the "CLI not found" fallback path.
_NO_CLAUDE_PATH = "/usr/bin:/bin"


@pytest.mark.platform_integrations
class TestClaudeInstall:
    """Test the Claude install flow."""

    def test_cli_absent_local_shows_correct_fallback_commands(self, temp_project_dir, install_runner):
        """When claude CLI is absent, manual instructions must include both required commands."""
        result = install_runner.run("install", platform="claude", env={"PATH": _NO_CLAUDE_PATH})

        assert "claude plugin marketplace add" in result.stdout
        assert "claude plugin install evolve-lite@evolve-marketplace" in result.stdout

    def test_cli_absent_local_uses_local_marketplace_source(self, temp_project_dir, install_runner):
        """When running from the repo root with no claude CLI, fallback should reference the local source."""
        result = install_runner.run("install", platform="claude", env={"PATH": _NO_CLAUDE_PATH})

        # Local run: SOURCE_DIR resolves to the repo, which has .claude-plugin/marketplace.json
        assert "local" in result.stdout or os.sep in result.stdout

    def test_cli_absent_remote_uses_github_marketplace_source(self, temp_project_dir, remote_install_runner):
        """When running from a remote context with no claude CLI, fallback should reference the GitHub repo."""
        result = remote_install_runner.run("install", platform="claude", env={"PATH": _NO_CLAUDE_PATH})

        assert "AgentToolkit/altk-evolve" in result.stdout
        assert "claude plugin marketplace add" in result.stdout
        assert "claude plugin install evolve-lite@evolve-marketplace" in result.stdout

    def test_cli_absent_exits_success(self, temp_project_dir, install_runner):
        """Missing claude CLI should warn but not fail the overall install."""
        result = install_runner.run("install", platform="claude", env={"PATH": _NO_CLAUDE_PATH})

        assert result.returncode == 0
