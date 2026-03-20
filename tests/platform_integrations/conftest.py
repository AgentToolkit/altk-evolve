"""
Pytest configuration and fixtures for platform-integrations install.sh tests.

All tests run in isolated temporary directories to avoid contaminating the repo.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "platform_integrations: tests for platform-integrations/install.sh")


@pytest.fixture
def temp_project_dir(tmp_path):
    """
    Create an isolated temporary directory for testing install.sh.

    This fixture ensures all tests run in a clean, isolated environment
    and automatically cleans up after the test completes.

    Returns:
        Path: Temporary directory path
    """
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    return project_dir


@pytest.fixture
def install_script():
    """
    Get the path to the install.sh script.

    Returns:
        Path: Absolute path to install.sh
    """
    repo_root = Path(__file__).parent.parent.parent
    script_path = repo_root / "platform-integrations" / "install.sh"
    assert script_path.exists(), f"install.sh not found at {script_path}"
    return script_path


@pytest.fixture
def platform_integrations_dir():
    """
    Get the path to the platform-integrations directory.

    Returns:
        Path: Absolute path to platform-integrations/
    """
    repo_root = Path(__file__).parent.parent.parent
    pi_dir = repo_root / "platform-integrations"
    assert pi_dir.exists(), f"platform-integrations/ not found at {pi_dir}"
    return pi_dir


class InstallRunner:
    """Helper class to run install.sh with various options."""

    def __init__(self, script_path: Path, project_dir: Path):
        self.script_path = script_path
        self.project_dir = project_dir
        self.last_result: Optional[subprocess.CompletedProcess[str]] = None

    def run(
        self,
        command: str,
        platform: Optional[str] = None,
        mode: Optional[str] = None,
        dry_run: bool = False,
        expect_success: bool = True,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        """
        Run install.sh with specified arguments.

        Args:
            command: Command to run (install, uninstall, status)
            platform: Platform to target (bob, roo, claude, all, or None for interactive)
            mode: Mode for bob (lite, full)
            dry_run: Whether to use --dry-run flag
            expect_success: Whether to expect the command to succeed
            env: Additional environment variables

        Returns:
            subprocess.CompletedProcess with stdout, stderr, returncode

        Raises:
            subprocess.CalledProcessError: If expect_success=True and command fails
        """
        # Build command with conditional arguments
        cmd = (
            ["bash", str(self.script_path), command, "--dir", str(self.project_dir)]
            + (["--platform", platform] if platform else [])
            + (["--mode", mode] if mode else [])
            + (["--dry-run"] if dry_run else [])
        )

        # Merge environment variables
        test_env = {**os.environ, **(env or {})}

        # Run the command
        result = subprocess.run(cmd, capture_output=True, text=True, env=test_env, check=expect_success)

        self.last_result = result
        return result


@pytest.fixture
def install_runner(install_script, temp_project_dir):
    """
    Create an InstallRunner instance for the test.

    Returns:
        InstallRunner: Helper to run install.sh commands
    """
    return InstallRunner(install_script, temp_project_dir)


class FileAssertions:
    """Helper class for file-related assertions."""

    @staticmethod
    def assert_file_exists(path: Path, message: str = ""):
        """Assert that a file exists."""
        assert path.is_file(), f"File does not exist: {path}. {message}"

    @staticmethod
    def assert_dir_exists(path: Path, message: str = ""):
        """Assert that a directory exists."""
        assert path.is_dir(), f"Directory does not exist: {path}. {message}"

    @staticmethod
    def assert_file_not_exists(path: Path, message: str = ""):
        """Assert that a file does not exist."""
        assert not path.exists(), f"File should not exist: {path}. {message}"

    @staticmethod
    def assert_dir_not_exists(path: Path, message: str = ""):
        """Assert that a directory does not exist."""
        assert not path.exists(), f"Directory should not exist: {path}. {message}"

    @staticmethod
    def assert_file_unchanged(path: Path, original_content: str):
        """Assert that a file's content has not changed."""
        assert path.is_file(), f"File does not exist: {path}"
        current_content = path.read_text()
        assert current_content == original_content, f"File content changed: {path}\nExpected:\n{original_content}\nGot:\n{current_content}"

    @staticmethod
    def assert_valid_json(path: Path):
        """Assert that a file contains valid JSON."""
        assert path.is_file(), f"File does not exist: {path}"
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise AssertionError(f"Invalid JSON in {path}: {e}")

    @staticmethod
    def assert_json_has_key(path: Path, key_path: list, message: str = ""):
        """
        Assert that a JSON file has a nested key.

        Args:
            path: Path to JSON file
            key_path: List of keys to traverse (e.g., ['mcpServers', 'kaizen'])
            message: Optional error message
        """
        assert path.is_file(), f"File does not exist: {path}"
        data = json.loads(path.read_text())

        cursor = data
        for key in key_path:
            assert key in cursor, f"Key path {key_path} not found in {path}. Missing key: {key}. {message}"
            cursor = cursor[key]

    @staticmethod
    def assert_json_not_has_key(path: Path, key_path: list, message: str = ""):
        """Assert that a JSON file does NOT have a nested key."""
        if not path.is_file():
            return  # File doesn't exist, so key doesn't exist

        data = json.loads(path.read_text())

        cursor = data
        for key in key_path[:-1]:
            if key not in cursor:
                return  # Key path doesn't exist
            cursor = cursor[key]

        assert key_path[-1] not in cursor, f"Key path {key_path} should not exist in {path}. {message}"

    @staticmethod
    def assert_sentinel_block_exists(path: Path, slug: str):
        """Assert that a YAML file contains sentinel comments for the given slug."""
        assert path.is_file(), f"File does not exist: {path}"
        content = path.read_text()
        start_sentinel = f"# >>>kaizen:{slug}<<<"
        end_sentinel = f"# <<<kaizen:{slug}<<<"

        assert start_sentinel in content, f"Start sentinel '{start_sentinel}' not found in {path}"
        assert end_sentinel in content, f"End sentinel '{end_sentinel}' not found in {path}"

    @staticmethod
    def assert_sentinel_block_not_exists(path: Path, slug: str):
        """Assert that a YAML file does NOT contain sentinel comments for the given slug."""
        if not path.is_file():
            return  # File doesn't exist, so sentinel doesn't exist

        content = path.read_text()
        start_sentinel = f"# >>>kaizen:{slug}<<<"
        end_sentinel = f"# <<<kaizen:{slug}<<<"

        assert start_sentinel not in content, f"Start sentinel '{start_sentinel}' should not be in {path}"
        assert end_sentinel not in content, f"End sentinel '{end_sentinel}' should not be in {path}"

    @staticmethod
    def read_json(path: Path) -> Dict[str, Any]:
        """Read and parse a JSON file."""
        result = json.loads(path.read_text())
        assert isinstance(result, dict), f"Expected dict from JSON file {path}"
        return result

    @staticmethod
    def write_json(path: Path, data: Dict[str, Any]):
        """Write data to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")

    @staticmethod
    def write_text(path: Path, content: str):
        """Write text to a file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


@pytest.fixture
def file_assertions():
    """
    Provide file assertion helpers.

    Returns:
        FileAssertions: Helper class with assertion methods
    """
    return FileAssertions()


class BobFixtures:
    """Helper class to create Bob platform test fixtures."""

    @staticmethod
    def create_existing_skill(project_dir: Path, skill_name: str = "my-custom-skill"):
        """Create a custom skill in .bob/skills/."""
        skill_dir = project_dir / ".bob" / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Create a SKILL.md file
        (skill_dir / "SKILL.md").write_text(f"# {skill_name}\n\nThis is a custom user skill.\n")

        # Create a script
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "run.py").write_text("#!/usr/bin/env python3\nprint('Custom skill')\n")

        return skill_dir

    @staticmethod
    def create_existing_command(project_dir: Path, command_name: str = "my-command"):
        """Create a custom command in .bob/commands/."""
        commands_dir = project_dir / ".bob" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)

        command_file = commands_dir / f"{command_name}.md"
        command_file.write_text(f"# {command_name}\n\nThis is a custom user command.\n")

        return command_file

    @staticmethod
    def create_existing_custom_modes(project_dir: Path):
        """Create a custom_modes.yaml with a user's custom mode."""
        custom_modes_file = project_dir / ".bob" / "custom_modes.yaml"
        custom_modes_file.parent.mkdir(parents=True, exist_ok=True)

        content = """customModes:
  - slug: my-mode
    name: My Custom Mode
    roleDefinition: |-
      This is my custom mode.
    customInstructions: |-
      Follow my custom instructions.
    groups:
      - read
      - edit
"""
        custom_modes_file.write_text(content)
        return custom_modes_file

    @staticmethod
    def create_existing_mcp_config(project_dir: Path):
        """Create an mcp.json with a user's custom MCP server."""
        mcp_file = project_dir / ".bob" / "mcp.json"
        mcp_file.parent.mkdir(parents=True, exist_ok=True)

        data = {"mcpServers": {"my-server": {"command": "node", "args": ["server.js"], "disabled": False}}}

        mcp_file.write_text(json.dumps(data, indent=2) + "\n")
        return mcp_file


class RooFixtures:
    """Helper class to create Roo platform test fixtures."""

    @staticmethod
    def create_existing_skill(project_dir: Path, skill_name: str = "my-roo-skill"):
        """Create a custom skill in .roo/skills/."""
        skill_dir = project_dir / ".roo" / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        (skill_dir / "SKILL.md").write_text(f"# {skill_name}\n\nThis is a custom Roo skill.\n")

        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "run.py").write_text("#!/usr/bin/env python3\nprint('Custom Roo skill')\n")

        return skill_dir

    @staticmethod
    def create_existing_roomodes_json(project_dir: Path):
        """Create a .roomodes file in JSON format with a custom mode."""
        roomodes_file = project_dir / ".roomodes"

        data = {
            "customModes": [
                {
                    "slug": "my-roo-mode",
                    "name": "My Roo Mode",
                    "roleDefinition": "This is my custom Roo mode.",
                    "customInstructions": "Follow my Roo instructions.",
                    "groups": ["read", "edit"],
                }
            ]
        }

        roomodes_file.write_text(json.dumps(data, indent=2) + "\n")
        return roomodes_file

    @staticmethod
    def create_existing_roomodes_yaml(project_dir: Path):
        """Create a .roomodes file in YAML format with a custom mode."""
        roomodes_file = project_dir / ".roomodes"

        content = """customModes:
  - slug: my-roo-mode
    name: My Roo Mode
    roleDefinition: |-
      This is my custom Roo mode.
    customInstructions: |-
      Follow my Roo instructions.
    groups:
      - read
      - edit
"""
        roomodes_file.write_text(content)
        return roomodes_file


@pytest.fixture
def bob_fixtures():
    """Provide Bob platform test fixtures."""
    return BobFixtures()


@pytest.fixture
def roo_fixtures():
    """Provide Roo platform test fixtures."""
    return RooFixtures()
