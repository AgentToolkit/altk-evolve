"""Tests for Bob's entity sharing functionality (subscribe, unsubscribe, sync, publish)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _load_claude_config_module():
    path = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite/lib/config.py"
    spec = importlib.util.spec_from_file_location("claude_evolve_lite_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cfg_module = _load_claude_config_module()

pytestmark = [pytest.mark.platform_integrations, pytest.mark.e2e]

_BOB_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/bob/evolve-lite"
_CLAUDE_LIB = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite/lib"
SUBSCRIBE_SCRIPT = _BOB_ROOT / "skills/evolve-lite:subscribe/scripts/subscribe.py"
UNSUBSCRIBE_SCRIPT = _BOB_ROOT / "skills/evolve-lite:unsubscribe/scripts/unsubscribe.py"
SYNC_SCRIPT = _BOB_ROOT / "skills/evolve-lite:sync/scripts/sync.py"
PUBLISH_SCRIPT = _BOB_ROOT / "skills/evolve-lite:publish/scripts/publish.py"
SAVE_SCRIPT = _BOB_ROOT / "skills/evolve-lite:learn/scripts/save_entities.py"
RETRIEVE_SCRIPT = _BOB_ROOT / "skills/evolve-lite:recall/scripts/retrieve_entities.py"


def run_script(script, project_dir, args=None, evolve_dir=None, stdin_data=None, expect_success=True):
    """Run a Bob script with proper environment setup.

    Injects Claude's lib directory into PYTHONPATH so Bob's scripts can import
    shared modules (config, audit, entity_io) without requiring a symlink in the repo.
    """
    env = {**os.environ}
    env["PYTHONPATH"] = str(_CLAUDE_LIB) + os.pathsep + env.get("PYTHONPATH", "")
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
        assert (evolve_dir / "entities" / "subscribed" / "alice").is_dir()
        assert (evolve_dir / "entities" / "subscribed" / "alice" / ".git").exists()

    def test_updates_config_with_repo_entry(self, temp_project_dir, local_repo):
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
        assert repos[0]["scope"] == "read"
        assert repos[0]["branch"] == "main"
        assert str(local_repo["bare"]) in repos[0]["remote"]

    def test_write_scope_recorded_in_config(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", "team", "--remote", str(local_repo["bare"]), "--branch", "main", "--scope", "write"],
            evolve_dir=evolve_dir,
        )
        cfg = cfg_module.load_config(str(temp_project_dir))
        repos = cfg_module.normalize_repos(cfg)
        assert repos[0]["scope"] == "write"

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
        actions = [e["action"] for e in entries]
        assert "subscribe" in actions
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


# ============================================================================
# Unsubscribe Tests
# ============================================================================


class TestBobUnsubscribe:
    """Tests for Bob's unsubscribe.py script."""

    def _subscribe(self, temp_project_dir, local_repo, name="alice", scope="read"):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            temp_project_dir,
            ["--name", name, "--remote", str(local_repo["bare"]), "--branch", "main", "--scope", scope],
            evolve_dir=evolve_dir,
        )
        return evolve_dir

    def test_removes_local_clone(self, temp_project_dir, local_repo):
        evolve_dir = self._subscribe(temp_project_dir, local_repo)
        run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--name", "alice"], evolve_dir=evolve_dir)
        assert not (evolve_dir / "entities" / "subscribed" / "alice").exists()

    def test_removes_repo_from_config(self, temp_project_dir, local_repo):
        evolve_dir = self._subscribe(temp_project_dir, local_repo)
        run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--name", "alice"], evolve_dir=evolve_dir)
        cfg = cfg_module.load_config(str(temp_project_dir))
        assert cfg_module.normalize_repos(cfg) == []

    def test_list_flag_prints_repos_as_json(self, temp_project_dir, local_repo):
        evolve_dir = self._subscribe(temp_project_dir, local_repo)
        result = run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--list"], evolve_dir=evolve_dir)
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert data[0]["name"] == "alice"
        assert data[0]["scope"] == "read"

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
        mirrored = evolve_dir / "entities" / "subscribed" / "alice"
        assert mirrored.exists(), "Subscription should have created this directory"
        (mirrored / "tip.md").write_text("---\ntype: guideline\n---\n\nA tip.\n")

        run_script(UNSUBSCRIBE_SCRIPT, temp_project_dir, ["--name", "alice"], evolve_dir=evolve_dir)
        assert not mirrored.exists()

    def test_list_empty_when_no_repos(self, temp_project_dir):
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


# ============================================================================
# Sync Tests
# ============================================================================


@pytest.fixture
def subscribed_project(temp_project_dir, local_repo):
    """A project already subscribed to local_repo (read-scope)."""
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
        guideline = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline" / "guideline-one.md"
        assert guideline.exists()
        assert "Always write tests." in guideline.read_text()

    def test_picks_up_new_entity_after_push(self, subscribed_project):
        p = subscribed_project
        lr = p["local_repo"]
        git_env = lr["env"]

        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])

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

        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])

        mirrored = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline" / "tip-two.md"
        assert mirrored.exists()
        assert "Delete dead code promptly." in mirrored.read_text()

    def test_quiet_flag_suppresses_output_when_no_changes(self, subscribed_project):
        p = subscribed_project
        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        result = run_script(SYNC_SCRIPT, p["project_dir"], ["--quiet"], evolve_dir=p["evolve_dir"])
        assert result.stdout.strip() == ""

    def test_no_repos_exits_cleanly(self, temp_project_dir):
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
        git_env = lr["env"]

        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])

        real_file = lr["work"] / "guideline" / "real.md"
        real_file.write_text("---\ntype: guideline\n---\n\nReal content.\n")
        symlink_file = lr["work"] / "guideline" / "link.md"
        symlink_file.symlink_to(real_file)

        subprocess.run(["git", "-C", str(lr["work"]), "add", "."], check=True, env=git_env)
        subprocess.run(
            ["git", "-C", str(lr["work"]), "commit", "-m", "add real file and symlink"],
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
        assert (mirrored / "real.md").exists(), "Real file should be present"
        assert (mirrored / "link.md").exists(), "Symlink should be present in git clone"
        # Symlinks are filtered out by the retrieve script, not by sync.

    def test_skips_invalid_subscription_name(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        cfg_path = temp_project_dir / "evolve.config.yaml"
        cfg_path.write_text('repos:\n  - name: "../outside"\n    scope: "read"\n    remote: "git@github.com:x/y.git"\n    branch: "main"\n')
        result = run_script(SYNC_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert result.returncode == 0
        assert "invalid subscription name" in result.stdout
        assert not (evolve_dir / "entities" / "subscribed" / "outside").exists()

    def test_rejects_dot_and_double_dot_names(self, temp_project_dir):
        """Sync must reject '.' and '..' repo names to prevent path traversal."""
        evolve_dir = temp_project_dir / ".evolve"
        cfg_path = temp_project_dir / "evolve.config.yaml"

        cfg_path.write_text('repos:\n  - name: "."\n    scope: "read"\n    remote: "git@github.com:x/y.git"\n    branch: "main"\n')
        result = run_script(SYNC_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert result.returncode == 0
        assert "invalid subscription name" in result.stdout

        cfg_path.write_text('repos:\n  - name: ".."\n    scope: "read"\n    remote: "git@github.com:x/y.git"\n    branch: "main"\n')
        result = run_script(SYNC_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert result.returncode == 0
        assert "invalid subscription name" in result.stdout

    def test_removed_entity_disappears_after_sync(self, subscribed_project):
        """Entities deleted from a read-scope remote are removed from the mirror on next sync."""
        p = subscribed_project
        lr = p["local_repo"]
        git_env = lr["env"]

        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        guideline_one = p["evolve_dir"] / "entities" / "subscribed" / "alice" / "guideline" / "guideline-one.md"
        assert guideline_one.exists()

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

        run_script(SYNC_SCRIPT, p["project_dir"], evolve_dir=p["evolve_dir"])
        assert not guideline_one.exists()


# ============================================================================
# Publish Tests
# ============================================================================


_WRITE_REPO_CONFIG = (
    "repos:\n"
    '  - name: "evolve-guidelines"\n'
    '    scope: "write"\n'
    '    remote: "git@github.com:alice/evolve-guidelines.git"\n'
    '    branch: "main"\n'
)


def _published_path(project_dir, filename, repo="evolve-guidelines"):
    return project_dir / ".evolve" / "entities" / "subscribed" / repo / "guideline" / filename


def _clone_write_target(project_dir, local_repo, repo="evolve-guidelines"):
    """Pre-create the local clone publish.py expects under entities/subscribed/<repo>/."""
    clone_dir = project_dir / ".evolve" / "entities" / "subscribed" / repo
    clone_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--branch", "main", str(local_repo["bare"]), str(clone_dir)],
        check=True,
        capture_output=True,
        env=local_repo["env"],
    )
    return clone_dir


class TestBobPublish:
    """Tests for Bob's publish.py script."""

    def test_moves_entity_to_write_repo_clone(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        entities_dir = evolve_dir / "entities" / "guideline"
        entities_dir.mkdir(parents=True)
        entity = entities_dir / "tip.md"
        entity.write_text("---\ntype: guideline\nvisibility: private\n---\n\nAlways test.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(_WRITE_REPO_CONFIG)
        _clone_write_target(temp_project_dir, local_repo)

        run_script(
            PUBLISH_SCRIPT,
            temp_project_dir,
            ["--entity", "tip.md", "--user", "alice"],
            evolve_dir=evolve_dir,
        )

        published = _published_path(temp_project_dir, "tip.md")
        assert published.exists()
        assert not entity.exists()
        content = published.read_text()
        assert "visibility: public" in content
        assert "owner: alice" in content
        assert "published_at:" in content

    def test_publish_writes_audit_with_repo_field(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        entities_dir = evolve_dir / "entities" / "guideline"
        entities_dir.mkdir(parents=True)
        (entities_dir / "tip.md").write_text("---\ntype: guideline\n---\n\nTest.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(_WRITE_REPO_CONFIG)
        _clone_write_target(temp_project_dir, local_repo)

        run_script(
            PUBLISH_SCRIPT,
            temp_project_dir,
            ["--entity", "tip.md", "--user", "alice"],
            evolve_dir=evolve_dir,
        )
        entries = [json.loads(line) for line in (evolve_dir / "audit.log").read_text().splitlines() if line.strip()]
        publish_entry = next(e for e in entries if e["action"] == "publish")
        assert publish_entry["actor"] == "alice"
        assert publish_entry["repo"] == "evolve-guidelines"

    def test_fails_if_entity_already_published(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        entities_dir = evolve_dir / "entities" / "guideline"
        entities_dir.mkdir(parents=True)
        source = entities_dir / "tip.md"
        source.write_text("---\ntype: guideline\n---\n\nNew version.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(_WRITE_REPO_CONFIG)
        _clone_write_target(temp_project_dir, local_repo)

        dest = _published_path(temp_project_dir, "tip.md")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("---\ntype: guideline\nvisibility: public\n---\n\nExisting.\n")

        result = run_script(
            PUBLISH_SCRIPT,
            temp_project_dir,
            ["--entity", "tip.md"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "already published" in result.stderr
        assert source.exists()

    def test_publish_errors_without_write_scope_repo(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        entities_dir = evolve_dir / "entities" / "guideline"
        entities_dir.mkdir(parents=True)
        (entities_dir / "tip.md").write_text("---\ntype: guideline\n---\n\nContent.\n")

        result = run_script(
            PUBLISH_SCRIPT,
            temp_project_dir,
            ["--entity", "tip.md"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "no write-scope repo" in result.stderr

    def test_publish_errors_when_target_clone_missing(self, temp_project_dir):
        """Publish refuses to run unless the write-scope clone has been created."""
        evolve_dir = temp_project_dir / ".evolve"
        entities_dir = evolve_dir / "entities" / "guideline"
        entities_dir.mkdir(parents=True)
        (entities_dir / "tip.md").write_text("---\ntype: guideline\n---\n\nContent.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(_WRITE_REPO_CONFIG)

        result = run_script(
            PUBLISH_SCRIPT,
            temp_project_dir,
            ["--entity", "tip.md"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "target repo clone not found" in result.stderr


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

    def test_deduplicates_exact_content(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        entity = {"type": "guideline", "content": "No magic numbers."}
        run_script(SAVE_SCRIPT, temp_project_dir, stdin_data=json.dumps({"entities": [entity]}), evolve_dir=evolve_dir)
        run_script(SAVE_SCRIPT, temp_project_dir, stdin_data=json.dumps({"entities": [entity]}), evolve_dir=evolve_dir)
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert len(files) == 1

    def test_dedup_is_case_and_whitespace_insensitive(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SAVE_SCRIPT,
            temp_project_dir,
            stdin_data=json.dumps({"entities": [{"type": "guideline", "content": "No magic numbers."}]}),
            evolve_dir=evolve_dir,
        )
        run_script(
            SAVE_SCRIPT,
            temp_project_dir,
            stdin_data=json.dumps({"entities": [{"type": "guideline", "content": "NO MAGIC  NUMBERS."}]}),
            evolve_dir=evolve_dir,
        )
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert len(files) == 1

    def test_multiple_entities_all_written(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SAVE_SCRIPT,
            temp_project_dir,
            stdin_data=json.dumps(
                {"entities": [{"type": "guideline", "content": "First tip."}, {"type": "guideline", "content": "Second tip."}]}
            ),
            evolve_dir=evolve_dir,
        )
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert len(files) == 2

    def test_skips_entities_without_content(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(SAVE_SCRIPT, temp_project_dir, stdin_data=json.dumps({"entities": [{"type": "guideline"}]}), evolve_dir=evolve_dir)
        guideline_dir = evolve_dir / "entities" / "guideline"
        if guideline_dir.exists():
            assert list(guideline_dir.glob("*.md")) == []

    def test_exits_cleanly_when_empty_entities_list(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_script(
            SAVE_SCRIPT, temp_project_dir, stdin_data=json.dumps({"entities": []}), evolve_dir=evolve_dir, expect_success=False
        )
        assert result.returncode == 0

    def test_output_reports_added_count(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_script(
            SAVE_SCRIPT,
            temp_project_dir,
            stdin_data=json.dumps({"entities": [{"type": "guideline", "content": "Tip A."}, {"type": "guideline", "content": "Tip B."}]}),
            evolve_dir=evolve_dir,
        )
        assert "Added 2" in result.stdout


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
        assert "Private tip" in result.stdout
        assert "## Entities for this task" in result.stdout

    def test_returns_published_entities_from_write_clone(self, temp_project_dir):
        """Published guidelines live in entities/subscribed/{repo}/guideline/."""
        evolve_dir = temp_project_dir / ".evolve"
        published_dir = evolve_dir / "entities" / "subscribed" / "my-memory" / "guideline"
        published_dir.mkdir(parents=True)
        (published_dir / "tip.md").write_text("---\ntype: guideline\nvisibility: public\n---\n\nPublished tip.\n")

        result = run_script(RETRIEVE_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert "Published tip" in result.stdout
        assert "[from: my-memory]" in result.stdout

    def test_returns_entities_from_subscribed_dir(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        subscribed_dir = evolve_dir / "entities" / "subscribed" / "alice" / "guideline"
        subscribed_dir.mkdir(parents=True)
        (subscribed_dir / "tip.md").write_text("---\ntype: guideline\n---\n\nSubscribed tip.\n")

        result = run_script(RETRIEVE_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert "Subscribed tip" in result.stdout
        assert "[from: alice]" in result.stdout

    def test_retrieve_filters_symlinked_entities(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        subscribed_dir = evolve_dir / "entities" / "subscribed" / "alice" / "guideline"
        subscribed_dir.mkdir(parents=True)
        real_file = subscribed_dir / "real.md"
        real_file.write_text("---\ntype: guideline\n---\n\nReal content.\n")
        link_file = subscribed_dir / "link.md"
        link_file.symlink_to(real_file)

        result = run_script(RETRIEVE_SCRIPT, temp_project_dir, evolve_dir=evolve_dir)
        assert "Real content" in result.stdout
        assert result.stdout.count("Real content") == 1, "Symlinked duplicate should be filtered out"


# Made with Bob
