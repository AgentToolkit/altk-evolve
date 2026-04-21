"""
Tests for the Claude platform integration installer behavior.

Claude install delegates entirely to the claude CLI via the marketplace workflow.
These tests control PATH to simulate the CLI being absent, which lets us verify
fallback output without needing the actual CLI installed.
"""

import pytest


# PATH that contains no claude binary — forces the "CLI not found" fallback path.
_NO_CLAUDE_PATH = "/usr/bin:/bin"


@pytest.mark.platform_integrations
class TestClaudeInstall:
    """Test the Claude install flow."""

    def test_cli_absent_prompts_download(self, temp_project_dir, install_runner):
        """When claude CLI is absent, user should be told to install it and re-run."""
        result = install_runner.run("install", platform="claude", env={"PATH": _NO_CLAUDE_PATH})

        assert "claude.ai/download" in result.stdout
        assert "re-run" in result.stdout

    def test_cli_absent_exits_success(self, temp_project_dir, install_runner):
        """Missing claude CLI should warn but not fail the overall install."""
        result = install_runner.run("install", platform="claude", env={"PATH": _NO_CLAUDE_PATH})

        assert result.returncode == 0
