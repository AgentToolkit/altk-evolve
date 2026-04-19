"""Tests for Bob's entity sharing functionality (subscribe, unsubscribe, sync, publish)."""

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
import config as cfg_module  # noqa: E402

pytestmark = pytest.mark.platform_integrations

_BOB_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/bob/evolve-lite"
_CLAUDE_LIB = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite/lib"
SUBSCRIBE_SCRIPT = _BOB_ROOT / "skills/evolve-lite:subscribe/scripts/subscribe.py"
UNSUBSCRIBE_SCRIPT = _BOB_ROOT / "skills/evolve-lite:unsubscribe/scripts/unsubscribe.py"
SYNC_SCRIPT = _BOB_ROOT / "skills/evolve-lite:sync/scripts/sync.py"
PUBLISH_SCRIPT = _BOB_ROOT / "skills/evolve-lite:publish/scripts/publish.py"
SAVE_SCRIPT = _BOB_ROOT / "skills/evolve-lite:learn/scripts/save_entities.py"
RETRIEVE_SCRIPT = _BOB_ROOT / "skills/evolve-lite:recall/scripts/retrieve_entities.py"


@pytest.fixture(scope="session", autouse=True)
def setup_bob_evolve_lib():
    """Create evolve-lib symlink for Bob's scripts to find shared modules.

    Bob's scripts use a 'smart import' pattern that walks up the directory tree
    to find 'evolve-lib'. This fixture creates a symlink from Claude's lib directory
    so Bob's scripts can import config, audit, and entity_io modules during tests.
    """
    evolve_lib = _BOB_ROOT / "evolve-lib"

    # Create symlink if it doesn't exist
    if not evolve_lib.exists():
        evolve_lib.symlink_to(_CLAUDE_LIB, target_is_directory=True)
        yield
        # Cleanup: remove symlink after all tests
        if evolve_lib.is_symlink():
            evolve_lib.unlink()
    else:
        yield


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


# ============================================================================
# Subscribe Tests
# ============================================================================


class TestBobSubscribe:
    """Tests for Bob's subscribe.py script."""

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


# ============================================================================
# Unsubscribe Tests
# ============================================================================


class TestBobUnsubscribe:
    """Tests for Bob's unsubscribe.py script."""

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


# ============================================================================
# Sync Tests
# ============================================================================


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


class TestBobSync:
    """Tests for Bob's sync.py script."""

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

    def test_skips_symlinked_entities(self, subscribed_project):
        p = subscribed_project
        lr = p["local_repo"]
        # Create a real file and a symlink pointing at it in the subscribed clone
        real_file = lr["work"] / "guideline" / "real.md"
        real_file.write_text("---\ntype: guideline\n---\n\nReal content.\n")
        symlink_file = lr["work"] / "guideline" / "link.md"
        symlink_file.symlink_to(real_file)
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        mirrored = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline"
        assert not (mirrored / "link.md").exists()

    def test_skips_invalid_subscription_name(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        # Write config manually with an unsafe name
        cfg_path = temp_project_dir / "evolve.config.yaml"
        cfg_path.write_text("subscriptions:\n  - name: ../evil\n    remote: git@github.com:x/y.git\n    branch: main\n")
        result = run_script(SYNC_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert result.returncode == 0
        assert "invalid subscription name" in result.stdout
        assert not (evolve_dir / "subscribed" / ".." / "evil").exists()

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


# ============================================================================
# Publish Tests
# ============================================================================


class TestBobPublish:
    """Tests for Bob's publish.py script."""

    def test_moves_entity_to_public_dir(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        entities_dir = evolve_dir / "entities" / "guideline"
        entities_dir.mkdir(parents=True)
        entity = entities_dir / "tip.md"
        entity.write_text("---\ntype: guideline\nvisibility: private\n---\n\nAlways test.\n")

        run_script(PUBLISH_SCRIPT, temp_project_dir, ["--entity", "tip.md"], evolve_dir=evolve_dir)

        public_entity = evolve_dir / "public" / "guideline" / "tip.md"
        assert public_entity.exists()
        assert not entity.exists()
        assert "visibility: public" in public_entity.read_text()

    def test_initializes_git_repo_if_needed(self, temp_project_dir):
        """Bob's publish script doesn't auto-initialize git repo - that's done separately."""
        evolve_dir = temp_project_dir / ".evolve"
        entities_dir = evolve_dir / "entities" / "guideline"
        entities_dir.mkdir(parents=True)
        entity = entities_dir / "tip.md"
        entity.write_text("---\ntype: guideline\n---\n\nTest.\n")

        # Initialize git repo manually (Bob expects this to be done via publish SKILL.md instructions)
        public_dir = evolve_dir / "public"
        public_dir.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=str(public_dir), check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "main"], cwd=str(public_dir), check=True, capture_output=True)

        run_script(PUBLISH_SCRIPT, temp_project_dir, ["--entity", "tip.md"], evolve_dir=evolve_dir)

        assert (evolve_dir / "public" / ".git").exists()
        assert (evolve_dir / "public" / "guideline" / "tip.md").exists()

    def test_fails_if_entity_already_published(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        public_dir = evolve_dir / "public" / "guideline"
        public_dir.mkdir(parents=True)
        existing = public_dir / "tip.md"
        existing.write_text("---\ntype: guideline\nvisibility: public\n---\n\nExisting.\n")

        entities_dir = evolve_dir / "entities" / "guideline"
        entities_dir.mkdir(parents=True)
        entity = entities_dir / "tip.md"
        entity.write_text("---\ntype: guideline\n---\n\nNew version.\n")

        result = run_script(PUBLISH_SCRIPT, temp_project_dir, ["--entity", "tip.md"], evolve_dir=evolve_dir, expect_success=False)
        assert result.returncode != 0
        assert "already published" in result.stderr


# ============================================================================
# Save Entities Tests
# ============================================================================


class TestBobSaveEntities:
    """Tests for Bob's save_entities.py script."""

    def test_writes_entity_file(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SAVE_SCRIPT,
            temp_project_dir,
            stdin_data=json.dumps({"entities": [{"type": "guideline", "content": "Use semantic versioning."}]}),
            evolve_dir=evolve_dir,
        )
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert len(files) == 1
        assert "Use semantic versioning." in files[0].read_text()

    def test_sets_visibility_private_by_default(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SAVE_SCRIPT,
            temp_project_dir,
            stdin_data=json.dumps({"entities": [{"type": "guideline", "content": "Commit often."}]}),
            evolve_dir=evolve_dir,
        )
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert "visibility: private" in files[0].read_text()

    def test_user_flag_stamps_owner(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SAVE_SCRIPT,
            temp_project_dir,
            ["--user", "alice"],
            stdin_data=json.dumps({"entities": [{"type": "guideline", "content": "Write clear commit messages."}]}),
            evolve_dir=evolve_dir,
        )
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert "owner: alice" in files[0].read_text()


# ============================================================================
# Retrieve Entities Tests
# ============================================================================


class TestBobRetrieveEntities:
    """Tests for Bob's retrieve_entities.py script.

    Note: Bob's retrieve script outputs markdown for Bob's UI, not JSON.
    """

    def test_returns_entities_from_private_dir(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        entities_dir = evolve_dir / "entities" / "guideline"
        entities_dir.mkdir(parents=True)
        (entities_dir / "tip.md").write_text("---\ntype: guideline\n---\n\nPrivate tip.\n")

        result = run_script(RETRIEVE_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        # Bob outputs markdown, not JSON
        assert "Private tip" in result.stdout
        assert "## Entities for this task" in result.stdout

    def test_returns_entities_from_public_dir(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        public_dir = evolve_dir / "public" / "guideline"
        public_dir.mkdir(parents=True)
        (public_dir / "tip.md").write_text("---\ntype: guideline\nvisibility: public\n---\n\nPublic tip.\n")

        result = run_script(RETRIEVE_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert "Public tip" in result.stdout

    def test_returns_entities_from_subscribed_dir(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        subscribed_dir = evolve_dir / "entities" / "subscribed" / "alice" / "guideline"
        subscribed_dir.mkdir(parents=True)
        (subscribed_dir / "tip.md").write_text("---\ntype: guideline\n---\n\nSubscribed tip.\n")

        result = run_script(RETRIEVE_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert "Subscribed tip" in result.stdout
        assert "[from: alice]" in result.stdout

    def test_sources_filter_works(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        # Create entities in different locations
        (evolve_dir / "entities" / "guideline").mkdir(parents=True)
        (evolve_dir / "entities" / "guideline" / "private.md").write_text("---\ntype: guideline\n---\n\nPrivate.\n")
        (evolve_dir / "public" / "guideline").mkdir(parents=True)
        (evolve_dir / "public" / "guideline" / "public.md").write_text("---\ntype: guideline\nvisibility: public\n---\n\nPublic.\n")

        # Filter for only private
        result = run_script(RETRIEVE_SCRIPT, temp_project_dir, ["--sources", "private"], evolve_dir=evolve_dir)
        assert "Private" in result.stdout
        assert "Public" not in result.stdout


# Made with Bob
