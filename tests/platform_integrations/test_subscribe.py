"""Tests for subscribe.py and unsubscribe.py."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_IS_WINDOWS = sys.platform == "win32"


def _load_claude_config_module():
    path = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite/lib/config.py"
    spec = importlib.util.spec_from_file_location("claude_evolve_lite_config_subscribe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cfg_module = _load_claude_config_module()

pytestmark = [pytest.mark.platform_integrations, pytest.mark.e2e]

_REPO_ROOT = Path(__file__).parent.parent.parent
CLAUDE_PLUGIN_ROOT = _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite"
CODEX_PLUGIN_ROOT = _REPO_ROOT / "platform-integrations/codex/plugins/evolve-lite"
SUBSCRIBE_SCRIPT = CLAUDE_PLUGIN_ROOT / "skills/subscribe/scripts/subscribe.py"
UNSUBSCRIBE_SCRIPT = CLAUDE_PLUGIN_ROOT / "skills/unsubscribe/scripts/unsubscribe.py"
SUBSCRIBE_SCRIPT_VARIANTS = [
    ("claude", CLAUDE_PLUGIN_ROOT / "skills/subscribe/scripts/subscribe.py"),
    ("codex", CODEX_PLUGIN_ROOT / "skills/subscribe/scripts/subscribe.py"),
]


def run_script(script, project_dir, args, evolve_dir=None, expect_success=True):
    env = {**os.environ}
    if evolve_dir:
        env["EVOLVE_DIR"] = str(evolve_dir)
    return subprocess.run(
        [sys.executable, str(script)] + args,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=expect_success,
    )


@pytest.mark.parametrize(("platform_name", "subscribe_script"), SUBSCRIBE_SCRIPT_VARIANTS)
@pytest.mark.parametrize("bad_name", ["foo/bar", "../etc", "alice:bob", "alice bob"])
def test_subscribe_rejects_invalid_name_characters(temp_project_dir, local_repo, subscribe_script, platform_name, bad_name):
    evolve_dir = temp_project_dir / ".evolve"
    result = run_script(
        subscribe_script,
        temp_project_dir,
        ["--name", bad_name, "--remote", str(local_repo["bare"]), "--branch", "main"],
        evolve_dir=evolve_dir,
        expect_success=False,
    )
    assert result.returncode != 0
    assert "invalid subscription name" in result.stderr


class TestSubscribe:
    def test_clones_remote_into_subscribed_dir(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        assert (evolve_dir / "entities" / "subscribed" / "alice").is_dir()
        assert (evolve_dir / "entities" / "subscribed" / "alice" / ".git").exists()

    def test_updates_config_with_subscription(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        cfg = cfg_module.load_config(str(temp_project_dir))
        repos = cfg_module.normalize_repos(cfg)
        assert len(repos) == 1
        assert repos[0]["name"] == "alice"
        assert repos[0]["branch"] == "main"
        assert repos[0]["scope"] == "read"
        assert str(local_repo["bare"]) in repos[0]["remote"]

    def test_write_scope_recorded_in_repos_list(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            [
                "--name",
                "memory",
                "--remote",
                str(local_repo["bare"]),
                "--branch",
                "main",
                "--scope",
                "write",
                "--notes",
                "shared project memory",
            ],
            evolve_dir=evolve_dir,
        )
        cfg = cfg_module.load_config(str(temp_project_dir))
        write = cfg_module.write_repos(cfg)
        assert len(write) == 1
        assert write[0]["name"] == "memory"
        assert write[0]["notes"] == "shared project memory"

    def test_writes_audit_log(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        log_path = temp_project_dir / ".evolve" / "audit.log"
        assert log_path.exists()
        entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
        subscribe_entry = next(e for e in entries if e["action"] == "subscribe")
        assert subscribe_entry["name"] == "alice"
        assert subscribe_entry["scope"] == "read"

    def test_fails_on_duplicate_name(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        args = ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"]
        run_script(SUBSCRIBE_SCRIPT, temp_project_dir, args, evolve_dir=evolve_dir)
        result = run_script(SUBSCRIBE_SCRIPT, temp_project_dir, args, evolve_dir=evolve_dir, expect_success=False)
        assert result.returncode != 0
        assert "already exists" in result.stderr

    def test_rejects_path_traversal_in_name(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "../../outside", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "invalid subscription name" in result.stderr

    def test_fails_when_dest_already_exists(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        dest = evolve_dir / "entities" / "subscribed" / "alice"
        dest.mkdir(parents=True)
        result = run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "already exists" in result.stderr
        cfg = cfg_module.load_config(str(temp_project_dir))
        assert cfg_module.normalize_repos(cfg) == []

    def test_rejects_empty_or_dot_name(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        for bad_name in [".", ""]:
            result = run_script(
                SUBSCRIBE_SCRIPT,
                temp_project_dir,
                ["--name", bad_name, "--remote", str(local_repo["bare"]), "--branch", "main"],
                evolve_dir=evolve_dir,
                expect_success=False,
            )
            assert result.returncode != 0, f"Expected failure for name={bad_name!r}"
            assert "invalid subscription name" in result.stderr

    def test_cloned_repo_contains_initial_entity(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        cloned = evolve_dir / "entities" / "subscribed" / "alice" / "guideline" / "guideline-one.md"
        assert cloned.exists()
        assert "Always write tests." in cloned.read_text()

    @pytest.mark.skipif(_IS_WINDOWS, reason="chmod not supported on Windows")
    def test_rolls_back_clone_if_config_write_fails(self, temp_project_dir, local_repo):
        """If save_config raises after a successful clone, the clone directory is removed."""
        evolve_dir = temp_project_dir / ".evolve"
        # Make the config file read-only so save_config raises PermissionError
        cfg_path = temp_project_dir / "evolve.config.yaml"
        cfg_path.write_text("subscriptions: []\n")
        cfg_path.chmod(0o444)
        try:
            result = run_script(
                SUBSCRIBE_SCRIPT,
                temp_project_dir,
                ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
                evolve_dir=evolve_dir,
                expect_success=False,
            )
        finally:
            cfg_path.chmod(0o644)
        assert result.returncode != 0
        assert "failed to record subscription" in result.stderr
        dest = evolve_dir / "entities" / "subscribed" / "alice"
        assert not dest.exists(), "Clone should be rolled back when config write fails"

    @pytest.mark.skipif(_IS_WINDOWS, reason="chmod not supported on Windows")
    def test_rolls_back_clone_if_audit_write_fails(self, temp_project_dir, local_repo):
        """If audit_append raises after a successful clone + config write, the clone is removed."""
        evolve_dir = temp_project_dir / ".evolve"
        evolve_dir.mkdir(parents=True)
        # Pre-create a read-only audit.log so audit_append raises PermissionError
        audit_log = evolve_dir / "audit.log"
        audit_log.write_text("")
        audit_log.chmod(0o444)
        try:
            result = run_script(
                SUBSCRIBE_SCRIPT,
                temp_project_dir,
                ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
                evolve_dir=evolve_dir,
                expect_success=False,
            )
        finally:
            audit_log.chmod(0o644)
        assert result.returncode != 0
        assert "failed to record subscription" in result.stderr
        dest = evolve_dir / "entities" / "subscribed" / "alice"
        assert not dest.exists(), "Clone should be rolled back when audit write fails"


class TestUnsubscribe:
    def _subscribe(self, temp_project_dir, local_repo, name="alice"):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", name, "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        return evolve_dir

    def test_removes_local_clone(self, temp_project_dir, local_repo):
        evolve_dir = self._subscribe(temp_project_dir, local_repo)
        run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--name", "alice"], evolve_dir=evolve_dir)
        assert not (evolve_dir / "entities" / "subscribed" / "alice").exists()

    def test_removes_subscription_from_config(self, temp_project_dir, local_repo):
        evolve_dir = self._subscribe(temp_project_dir, local_repo)
        run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--name", "alice"], evolve_dir=evolve_dir)
        cfg = cfg_module.load_config(str(temp_project_dir))
        assert cfg_module.normalize_repos(cfg) == []

    def test_list_flag_prints_subscriptions_as_json(self, temp_project_dir, local_repo):
        evolve_dir = self._subscribe(temp_project_dir, local_repo)
        result = run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--list"], evolve_dir=evolve_dir)
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert data[0]["name"] == "alice"

    def test_fails_when_name_not_found(self, temp_project_dir, local_repo):
        evolve_dir = self._subscribe(temp_project_dir, local_repo)
        result = run_script(
            UNSUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "nonexistent"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "not found" in result.stderr

    def test_removes_mirrored_entities(self, temp_project_dir, local_repo):
        evolve_dir = self._subscribe(temp_project_dir, local_repo)
        cloned = evolve_dir / "entities" / "subscribed" / "alice"
        assert cloned.is_dir()

        run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--name", "alice"], evolve_dir=evolve_dir)
        assert not cloned.exists()

    def test_list_empty_when_no_subscriptions(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--list"], evolve_dir=evolve_dir)
        data = json.loads(result.stdout)
        assert data == []

    def test_rejects_path_traversal_in_name(self, temp_project_dir, local_repo):
        evolve_dir = self._subscribe(temp_project_dir, local_repo)
        result = run_script(
            UNSUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "../../outside"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "invalid subscription name" in result.stderr
