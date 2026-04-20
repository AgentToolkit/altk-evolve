"""
Pytest configuration and fixtures for platform-integrations install.sh tests.

All tests run in isolated temporary directories to avoid contaminating the repo.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "platform_integrations: tests for platform-integrations/install.sh")
    config.addinivalue_line("markers", "integration: tests that require git and perform subprocess I/O")


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
            platform: Platform to target (bob, claude, codex, all, or None for interactive)
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
    def assert_file_contains(path: Path, text: str, message: str = ""):
        """Assert that a file contains the specified text."""
        assert path.is_file(), f"File does not exist: {path}. {message}"
        content = path.read_text()
        assert text in content, f"Text not found in {path}. {message}\nLooking for: {text}\nFile content:\n{content}"

    @staticmethod
    def assert_dir_empty(path: Path, message: str = ""):
        """Assert that a directory is empty or doesn't exist."""
        if not path.exists():
            return  # Directory doesn't exist, so it's "empty"
        assert path.is_dir(), f"Path exists but is not a directory: {path}. {message}"
        contents = list(path.iterdir())
        assert len(contents) == 0, f"Directory is not empty: {path}. Contains: {[str(p) for p in contents]}. {message}"

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
            key_path: List of keys to traverse (e.g., ['mcpServers', 'evolve'])
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
        start_sentinel = f"# >>>evolve:{slug}<<<"
        end_sentinel = f"# <<<evolve:{slug}<<<"

        assert start_sentinel in content, f"Start sentinel '{start_sentinel}' not found in {path}"
        assert end_sentinel in content, f"End sentinel '{end_sentinel}' not found in {path}"

    @staticmethod
    def assert_sentinel_block_not_exists(path: Path, slug: str):
        """Assert that a YAML file does NOT contain sentinel comments for the given slug."""
        if not path.is_file():
            return  # File doesn't exist, so sentinel doesn't exist

        content = path.read_text()
        start_sentinel = f"# >>>evolve:{slug}<<<"
        end_sentinel = f"# <<<evolve:{slug}<<<"

        assert start_sentinel not in content, f"Start sentinel '{start_sentinel}' should not be in {path}"
        assert end_sentinel not in content, f"End sentinel '{end_sentinel}' should not be in {path}"

    @staticmethod
    def assert_all_bob_skills_installed(bob_dir: Path):
        """Assert every skill in the bob/evolve-lite source tree is installed."""
        repo_root = Path(__file__).parent.parent.parent
        skills_src = repo_root / "platform-integrations" / "bob" / "evolve-lite" / "skills"
        for skill_dir in sorted(skills_src.iterdir()):
            if skill_dir.is_dir():
                FileAssertions.assert_dir_exists(bob_dir / "skills" / skill_dir.name)

    @staticmethod
    def assert_all_bob_commands_installed(bob_dir: Path):
        """Assert every evolve-lite command in the source tree is installed."""
        repo_root = Path(__file__).parent.parent.parent
        commands_src = repo_root / "platform-integrations" / "bob" / "evolve-lite" / "commands"
        for cmd_file in sorted(commands_src.glob("evolve-lite:*.md")):
            FileAssertions.assert_file_exists(bob_dir / "commands" / cmd_file.name)

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

    @staticmethod
    def create_existing_mcp_config_with_evolve(project_dir: Path):
        """Create an mcp.json with a user-customized evolve server entry."""
        mcp_file = project_dir / ".bob" / "mcp.json"
        mcp_file.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "mcpServers": {
                "my-server": {"command": "node", "args": ["server.js"], "disabled": False},
                "evolve": {
                    "command": "python3",
                    "args": ["old_evolve.py"],
                    "disabled": True,
                    "env": {"EVOLVE_PROFILE": "local"},
                    "metadata": {"managedBy": "user"},
                },
            }
        }

        mcp_file.write_text(json.dumps(data, indent=2) + "\n")
        return mcp_file


class CodexFixtures:
    """Helper class to create Codex platform test fixtures."""

    @staticmethod
    def create_existing_plugin(project_dir: Path, plugin_name: str = "my-codex-plugin"):
        """Create a custom plugin in plugins/."""
        plugin_dir = project_dir / "plugins" / plugin_name / ".codex-plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": plugin_name,
                    "version": "0.1.0",
                    "description": "Custom user plugin",
                    "skills": "./skills/",
                },
                indent=2,
            )
            + "\n"
        )
        return plugin_dir.parent

    @staticmethod
    def create_existing_marketplace(project_dir: Path):
        """Create a marketplace.json with a user's existing Codex plugin entry."""
        marketplace_file = project_dir / ".agents" / "plugins" / "marketplace.json"
        marketplace_file.parent.mkdir(parents=True, exist_ok=True)
        marketplace_file.write_text(
            json.dumps(
                {
                    "name": "custom-local",
                    "interface": {
                        "displayName": "Custom Local Plugins",
                    },
                    "plugins": [
                        {
                            "name": "my-codex-plugin",
                            "source": {
                                "source": "local",
                                "path": "./plugins/my-codex-plugin",
                            },
                            "policy": {
                                "installation": "AVAILABLE",
                                "authentication": "ON_INSTALL",
                            },
                            "category": "Productivity",
                        }
                    ],
                },
                indent=2,
            )
            + "\n"
        )
        return marketplace_file

    @staticmethod
    def create_existing_hooks(project_dir: Path):
        """Create a .codex/hooks.json with a user's existing hooks."""
        hooks_file = project_dir / ".codex" / "hooks.json"
        hooks_file.parent.mkdir(parents=True, exist_ok=True)
        hooks_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "startup|resume",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 ~/.codex/hooks/session_start.py",
                                        "statusMessage": "Loading notes",
                                    }
                                ],
                            }
                        ],
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 ~/.codex/hooks/custom_prompt_memory.py",
                                        "statusMessage": "Loading custom memory",
                                    }
                                ]
                            }
                        ],
                    }
                },
                indent=2,
            )
            + "\n"
        )
        return hooks_file

    @staticmethod
    def create_existing_hooks_with_shared_evolve_group(project_dir: Path):
        """Create a list-based UserPromptSubmit group containing both user hooks and the evolve hook."""
        hooks_file = project_dir / ".codex" / "hooks.json"
        hooks_file.parent.mkdir(parents=True, exist_ok=True)
        hooks_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "matcher": "src/.*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 ~/.codex/hooks/custom_prompt_memory.py",
                                        "statusMessage": "Loading custom memory",
                                    },
                                    {
                                        "type": "command",
                                        "command": (
                                            "sh -lc '"
                                            'd="$PWD"; '
                                            "while :; do "
                                            'candidate="$d/plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py"; '
                                            'if [ -f "$candidate" ]; then exec python3 "$candidate"; fi; '
                                            '[ "$d" = "/" ] && break; '
                                            'd="$(dirname "$d")"; '
                                            "done; "
                                            "exit 1'"
                                        ),
                                        "statusMessage": "Old evolve guidance",
                                        "delayMs": 250,
                                    },
                                ],
                            }
                        ]
                    }
                },
                indent=2,
            )
            + "\n"
        )
        return hooks_file

    @staticmethod
    def create_existing_hooks_with_dict_evolve_group(project_dir: Path):
        """Create a dict-based UserPromptSubmit group containing user hooks and the evolve hook."""
        hooks_file = project_dir / ".codex" / "hooks.json"
        hooks_file.parent.mkdir(parents=True, exist_ok=True)
        hooks_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "matcher": "src/.*",
                                "hooks": {
                                    "memory": {
                                        "type": "command",
                                        "command": "python3 ~/.codex/hooks/custom_prompt_memory.py",
                                        "statusMessage": "Loading custom memory",
                                    },
                                    "evolve-lite": {
                                        "type": "command",
                                        "command": (
                                            "sh -lc '"
                                            'd="$PWD"; '
                                            "while :; do "
                                            'candidate="$d/plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py"; '
                                            'if [ -f "$candidate" ]; then exec python3 "$candidate"; fi; '
                                            '[ "$d" = "/" ] && break; '
                                            'd="$(dirname "$d")"; '
                                            "done; "
                                            "exit 1'"
                                        ),
                                        "statusMessage": "Old evolve guidance",
                                        "delayMs": 250,
                                    },
                                },
                            }
                        ]
                    }
                },
                indent=2,
            )
            + "\n"
        )
        return hooks_file


@pytest.fixture
def remote_install_script(tmp_path_factory):
    """
    Copy install.sh to an isolated temp dir that has no platform-integrations/ sibling.

    This simulates the curl | bash scenario where the script runs from a directory
    that is not the repo root, so SCRIPT_DIR points to no local source tree.

    Returns:
        Path: Path to the copied install.sh
    """
    repo_root = Path(__file__).parent.parent.parent
    src = repo_root / "platform-integrations" / "install.sh"
    assert src.exists(), f"install.sh not found at {src}"

    isolated_dir = tmp_path_factory.mktemp("remote_script")
    dst = isolated_dir / "install.sh"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture
def remote_install_runner(remote_install_script, temp_project_dir):
    """
    InstallRunner backed by the isolated (remote-simulating) install.sh copy.

    Returns:
        InstallRunner: Helper to run install.sh commands from a dir with no local source
    """
    return InstallRunner(remote_install_script, temp_project_dir)


@pytest.fixture
def bob_fixtures():
    """Provide Bob platform test fixtures."""
    return BobFixtures()


@pytest.fixture
def codex_fixtures():
    """Provide Codex platform test fixtures."""
    return CodexFixtures()


# ---------------------------------------------------------------------------
# Evolve-lite plugin fixtures
# ---------------------------------------------------------------------------

EVOLVE_PLUGIN_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite"


@pytest.fixture
def git_env():
    """git environment with author info set so commits work in CI without ~/.gitconfig."""
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test User",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }


@pytest.fixture
def local_repo(tmp_path, git_env):
    """A local bare git repo acting as a mock remote for subscribe/sync tests.

    Returns a dict:
      bare  — Path to the bare repo (pass as --remote to subscribe.py)
      work  — Path to a working clone (push new commits here to simulate updates)
      env   — git env dict (reuse for any git subprocess calls in tests)
    """
    # 1. Init a scratch working dir and pin the default branch to 'main'
    init = tmp_path / "init_work"
    init.mkdir()
    subprocess.run(["git", "init", str(init)], check=True, capture_output=True, env=git_env)
    subprocess.run(
        ["git", "-C", str(init), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        capture_output=True,
        env=git_env,
    )

    # Seed one entity
    guideline = init / "guideline"
    guideline.mkdir()
    (guideline / "guideline-one.md").write_text("---\ntype: guideline\n---\n\nAlways write tests.\n")
    subprocess.run(["git", "-C", str(init), "add", "."], check=True, capture_output=True, env=git_env)
    subprocess.run(
        ["git", "-C", str(init), "commit", "-m", "init"],
        check=True,
        capture_output=True,
        env=git_env,
    )

    # 2. Create a bare clone (this is the "remote")
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(init), str(bare)],
        check=True,
        capture_output=True,
        env=git_env,
    )

    # 3. Create a working clone of the bare (for pushing new commits in tests)
    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", str(bare), str(work)],
        check=True,
        capture_output=True,
        env=git_env,
    )

    return {"bare": bare, "work": work, "env": git_env}
