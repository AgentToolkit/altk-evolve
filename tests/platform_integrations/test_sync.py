"""Tests for skills/sync/scripts/sync.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.platform_integrations, pytest.mark.e2e]

_REPO_ROOT = Path(__file__).parent.parent.parent
CLAUDE_PLUGIN_ROOT = _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite"
CODEX_PLUGIN_ROOT = _REPO_ROOT / "platform-integrations/codex/plugins/evolve-lite"
SUBSCRIBE_SCRIPT = CLAUDE_PLUGIN_ROOT / "skills/subscribe/scripts/subscribe.py"
SYNC_SCRIPT = CLAUDE_PLUGIN_ROOT / "skills/sync/scripts/sync.py"
RETRIEVE_SCRIPT = CODEX_PLUGIN_ROOT / "skills/recall/scripts/retrieve_entities.py"
SYNC_SCRIPT_VARIANTS = [
    ("claude", CLAUDE_PLUGIN_ROOT / "skills/sync/scripts/sync.py"),
    ("codex", CODEX_PLUGIN_ROOT / "skills/sync/scripts/sync.py"),
]


def run_script(script, project_dir, args=None, evolve_dir=None, stdin_data=None, expect_success=True):
    env = {**os.environ}
    if evolve_dir:
        env["EVOLVE_DIR"] = str(evolve_dir)
    return subprocess.run(
        [sys.executable, str(script)] + (args or []),
        input=stdin_data,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=expect_success,
    )


@pytest.mark.parametrize(("platform_name", "sync_script"), SYNC_SCRIPT_VARIANTS)
@pytest.mark.parametrize(
    "config_text",
    [
        "subscriptions:\n  - name: 123\n    remote: git@github.com:x/y.git\n    branch: main\n",
        "subscriptions:\n  - name: alice\n    remote: git@github.com:x/y.git\n    branch: 123\n",
        'subscriptions:\n  - name: "   "\n    remote: git@github.com:x/y.git\n    branch: main\n',
        'subscriptions:\n  - name: alice\n    remote: git@github.com:x/y.git\n    branch: "   "\n',
    ],
)
def test_sync_skips_malformed_subscription_entries(temp_project_dir, sync_script, platform_name, config_text):
    evolve_dir = temp_project_dir / ".evolve"
    cfg_path = temp_project_dir / "evolve.config.yaml"
    cfg_path.write_text(config_text)

    result = run_script(sync_script, temp_project_dir, evolve_dir=evolve_dir)
    assert result.returncode == 0
    assert "skipped" in result.stdout
    assert "Traceback" not in result.stderr
    assert not (evolve_dir / "entities" / "subscribed" / "alice").exists()


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
        guideline = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline" / "guideline-one.md"
        assert guideline.exists()
        assert "Always write tests." in guideline.read_text()

    def test_picks_up_new_entity_after_push(self, subscribed_project):
        """After a new entity is pushed to the remote, a second sync picks it up."""
        p = subscribed_project
        lr = p["local_repo"]
        git_env = lr["env"]

        # First sync — brings down the initial entity
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])

        # Push a new entity to the remote via the working clone
        new_entity = lr["work"] / "guideline" / "guideline-two.md"
        new_entity.write_text("---\ntype: guideline\n---\n\nDelete dead code promptly.\n")
        subprocess.run(["git", "-C", str(lr["work"]), "add", "."], check=True, env=git_env)
        subprocess.run(
            ["git", "-C", str(lr["work"]), "commit", "-m", "add guideline-two"],
            check=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(lr["work"]), "push", "origin", "main"],
            check=True,
            env=git_env,
        )

        # Second sync — should pick up guideline-two
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])

        mirrored = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline" / "guideline-two.md"
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

    def test_sync_preserves_symlinks_in_clone(self, subscribed_project):
        p = subscribed_project
        lr = p["local_repo"]
        # Create a real file and a symlink pointing at it in the subscribed clone
        real_file = lr["work"] / "guideline" / "real.md"
        real_file.write_text("---\ntype: guideline\n---\n\nReal content.\n")
        symlink_file = lr["work"] / "guideline" / "link.md"
        symlink_file.symlink_to(real_file)
        git_env = lr["env"]
        subprocess.run(["git", "-C", str(lr["work"]), "add", "."], check=True, env=git_env)
        subprocess.run(
            ["git", "-C", str(lr["work"]), "commit", "-m", "add symlinked entity"],
            check=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(lr["work"]), "push", "origin", "main"],
            check=True,
            env=git_env,
        )
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        mirrored = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline"
        assert (mirrored / "link.md").exists()

        env = {**os.environ, "EVOLVE_DIR": str(p["evolve_dir"])}
        result = subprocess.run(
            [sys.executable, str(RETRIEVE_SCRIPT)],
            input=json.dumps({"prompt": "How do I write clean code?"}),
            capture_output=True,
            text=True,
            cwd=str(p["project_dir"]),
            env=env,
            check=False,
        )

        assert result.returncode == 0
        assert "Real content." in result.stdout
        assert "link.md" not in result.stdout

    def test_skips_invalid_subscription_name(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        # Write config manually with an unsafe name
        cfg_path = temp_project_dir / "evolve.config.yaml"
        cfg_path.write_text("subscriptions:\n  - name: ../outside\n    remote: git@github.com:x/y.git\n    branch: main\n")
        result = run_script(SYNC_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert result.returncode == 0
        assert "invalid subscription name" in result.stdout
        assert not (evolve_dir / "entities" / "outside").exists()

    def test_manual_run_ignores_on_session_start_false(self, subscribed_project):
        p = subscribed_project
        cfg_path = p["project_dir"] / "evolve.config.yaml"
        cfg_path.write_text("sync:\n  on_session_start: false\nsubscriptions:\n  - name: alice\n    remote: x\n    branch: main\n")
        # Manual run (no --quiet) must still execute even with on_session_start: false
        result = run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        assert result.returncode == 0
        assert "Synced" in result.stdout

    def test_removed_entity_disappears_after_sync(self, subscribed_project):
        """Entities deleted from the remote are removed from the mirror on next sync."""
        p = subscribed_project
        lr = p["local_repo"]
        git_env = lr["env"]

        # First sync
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        guideline_one = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline" / "guideline-one.md"
        assert guideline_one.exists()

        # Delete guideline-one from remote
        subprocess.run(
            ["git", "-C", str(lr["work"]), "rm", "guideline/guideline-one.md"],
            check=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(lr["work"]), "commit", "-m", "remove guideline-one"],
            check=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(lr["work"]), "push", "origin", "main"],
            check=True,
            env=git_env,
        )

        # Second sync — mirror is cleared and re-copied without guideline-one
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        assert not guideline_one.exists()
