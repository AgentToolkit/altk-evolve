"""Tests for skills/sync/scripts/sync.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_PLUGIN_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite"
SUBSCRIBE_SCRIPT = _PLUGIN_ROOT / "skills/subscribe/scripts/subscribe.py"
SYNC_SCRIPT = _PLUGIN_ROOT / "skills/sync/scripts/sync.py"


def run_script(script, project_dir, args=None, evolve_dir=None, expect_success=True):
    env = {**os.environ}
    if evolve_dir:
        env["EVOLVE_DIR"] = str(evolve_dir)
    return subprocess.run(
        [sys.executable, str(script)] + (args or []),
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=expect_success,
    )


@pytest.fixture
def subscribed_project(temp_project_dir, local_repo):
    """A project already subscribed to local_repo."""
    evolve_dir = temp_project_dir / ".evolve"
    run_script(
        SUBSCRIBE_SCRIPT,
        temp_project_dir,
        ["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
        evolve_dir=evolve_dir,
    )
    return {"project_dir": temp_project_dir, "evolve_dir": evolve_dir, "local_repo": local_repo}


class TestSync:
    def test_mirrors_entities_into_subscribed_dir(self, subscribed_project):
        p = subscribed_project
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        mirrored = p["evolve_dir"] / "entities" / "subscribed" / "alice"
        assert mirrored.is_dir()
        assert any(mirrored.rglob("*.md"))

    def test_mirrors_initial_entity_content(self, subscribed_project):
        p = subscribed_project
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        tip = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline" / "tip-one.md"
        assert tip.exists()
        assert "Always write tests." in tip.read_text()

    def test_picks_up_new_entity_after_push(self, subscribed_project):
        """After a new entity is pushed to the remote, a second sync picks it up."""
        p = subscribed_project
        lr = p["local_repo"]
        git_env = lr["env"]

        # First sync — brings down the initial entity
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])

        # Push a new entity to the remote via the working clone
        new_entity = lr["work"] / "guideline" / "tip-two.md"
        new_entity.write_text("---\ntype: guideline\n---\n\nDelete dead code promptly.\n")
        subprocess.run(["git", "-C", str(lr["work"]), "add", "."], check=True, env=git_env)
        subprocess.run(
            ["git", "-C", str(lr["work"]), "commit", "-m", "add tip-two"],
            check=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(lr["work"]), "push", "origin", "main"],
            check=True,
            env=git_env,
        )

        # Second sync — should pick up tip-two
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])

        mirrored = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline" / "tip-two.md"
        assert mirrored.exists()
        assert "Delete dead code promptly." in mirrored.read_text()

    def test_quiet_flag_suppresses_output_when_no_changes(self, subscribed_project):
        p = subscribed_project
        # First sync to reach a clean state
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        # Second sync with --quiet: nothing changed, no output expected
        result = run_script(SYNC_SCRIPT, p["project_dir"], ["--quiet"], evolve_dir=p["evolve_dir"])
        assert result.stdout.strip() == ""

    def test_no_subscriptions_exits_cleanly(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_script(SYNC_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert result.returncode == 0
        assert "No subscriptions" in result.stdout

    def test_writes_audit_log(self, subscribed_project):
        p = subscribed_project
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        log_path = p["project_dir"] / ".evolve" / "audit.log"
        assert log_path.exists()
        actions = [json.loads(line)["action"] for line in log_path.read_text().splitlines() if line.strip()]
        assert "sync" in actions

    def test_removed_entity_disappears_after_sync(self, subscribed_project):
        """Entities deleted from the remote are removed from the mirror on next sync."""
        p = subscribed_project
        lr = p["local_repo"]
        git_env = lr["env"]

        # First sync
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        tip_one = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline" / "tip-one.md"
        assert tip_one.exists()

        # Delete tip-one from remote
        subprocess.run(
            ["git", "-C", str(lr["work"]), "rm", "guideline/tip-one.md"],
            check=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(lr["work"]), "commit", "-m", "remove tip-one"],
            check=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(lr["work"]), "push", "origin", "main"],
            check=True,
            env=git_env,
        )

        # Second sync — mirror is cleared and re-copied without tip-one
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        assert not tip_one.exists()
