"""Tests for subscribe.py and unsubscribe.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite/lib"),
)
import config as cfg_module

pytestmark = pytest.mark.platform_integrations

_PLUGIN_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite"
SUBSCRIBE_SCRIPT = _PLUGIN_ROOT / "skills/subscribe/scripts/subscribe.py"
UNSUBSCRIBE_SCRIPT = _PLUGIN_ROOT / "skills/unsubscribe/scripts/unsubscribe.py"


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


class TestSubscribe:
    def test_clones_remote_into_subscribed_dir(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        assert (evolve_dir / "subscribed" / "alice").is_dir()
        assert (evolve_dir / "subscribed" / "alice" / ".git").exists()

    def test_updates_config_with_subscription(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        cfg = cfg_module.load_config(str(temp_project_dir))
        subs = cfg.get("subscriptions", [])
        assert len(subs) == 1
        assert subs[0]["name"] == "alice"
        assert subs[0]["branch"] == "main"
        assert str(local_repo["bare"]) in subs[0]["remote"]

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
        entry = json.loads(log_path.read_text().strip())
        assert entry["action"] == "subscribe"
        assert entry["name"] == "alice"

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
            ["--name", "../../evil", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "invalid subscription name" in result.stderr

    def test_fails_when_dest_already_exists(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        dest = evolve_dir / "subscribed" / "alice"
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
        assert cfg.get("subscriptions", []) == []

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
        cloned = evolve_dir / "subscribed" / "alice" / "guideline" / "tip-one.md"
        assert cloned.exists()
        assert "Always write tests." in cloned.read_text()


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
        assert not (evolve_dir / "subscribed" / "alice").exists()

    def test_removes_subscription_from_config(self, temp_project_dir, local_repo):
        evolve_dir = self._subscribe(temp_project_dir, local_repo)
        run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--name", "alice"], evolve_dir=evolve_dir)
        cfg = cfg_module.load_config(str(temp_project_dir))
        assert cfg.get("subscriptions", []) == []

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
        # Simulate mirrored entities (sync would create these)
        mirrored = evolve_dir / "entities" / "subscribed" / "alice"
        mirrored.mkdir(parents=True)
        (mirrored / "tip.md").write_text("---\ntype: guideline\n---\n\nA tip.\n")

        run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--name", "alice"], evolve_dir=evolve_dir)
        assert not mirrored.exists()

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
            ["--name", "../../evil"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "invalid subscription name" in result.stderr
