"""Tests for the Codex sharing scripts."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.platform_integrations, pytest.mark.e2e]

_PLUGIN_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/codex/plugins/evolve-lite"
SAVE_SCRIPT = _PLUGIN_ROOT / "skills/learn/scripts/save_entities.py"
RETRIEVE_SCRIPT = _PLUGIN_ROOT / "skills/recall/scripts/retrieve_entities.py"
PUBLISH_SCRIPT = _PLUGIN_ROOT / "skills/publish/scripts/publish.py"
SUBSCRIBE_SCRIPT = _PLUGIN_ROOT / "skills/subscribe/scripts/subscribe.py"
UNSUBSCRIBE_SCRIPT = _PLUGIN_ROOT / "skills/unsubscribe/scripts/unsubscribe.py"
SYNC_SCRIPT = _PLUGIN_ROOT / "skills/sync/scripts/sync.py"
HOOK_INPUT = json.dumps({"prompt": "How do I write clean code?"})


def run_script(script, project_dir, args=None, evolve_dir=None, stdin=None, expect_success=True):
    env = {**os.environ}
    if evolve_dir:
        env["EVOLVE_DIR"] = str(evolve_dir)
    return subprocess.run(
        [sys.executable, str(script)] + (args or []),
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=expect_success,
    )


class TestCodexSaveAndRetrieve:
    def test_save_stamps_owner_and_private_visibility(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SAVE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--user", "alice"],
            evolve_dir=evolve_dir,
            stdin=json.dumps({"entities": [{"type": "guideline", "content": "Write clear commit messages."}]}),
        )
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "owner: alice" in content
        assert "visibility: private" in content

    def test_save_ignores_incoming_owner_and_visibility(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SAVE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--user", "alice"],
            evolve_dir=evolve_dir,
            stdin=json.dumps(
                {
                    "entities": [
                        {
                            "type": "guideline",
                            "content": "Prefer small, reversible changes.",
                            "owner": "mallory",
                            "visibility": "public",
                        }
                    ]
                }
            ),
        )
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "owner: alice" in content
        assert "owner: mallory" not in content
        assert "visibility: private" in content
        assert "visibility: public" not in content

    def test_retrieve_annotates_subscribed_entities(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        own_dir = evolve_dir / "entities" / "guideline"
        own_dir.mkdir(parents=True)
        (own_dir / "guideline.md").write_text("---\ntype: guideline\n---\n\nKeep functions small.\n")
        sub_dir = evolve_dir / "entities" / "subscribed" / "alice" / "guideline"
        sub_dir.mkdir(parents=True)
        (sub_dir / "alice-guideline.md").write_text("---\ntype: guideline\nowner: alice\nvisibility: public\n---\n\nAlways write tests.\n")

        result = run_script(
            RETRIEVE_SCRIPT,
            project_dir=temp_project_dir,
            evolve_dir=evolve_dir,
            stdin=HOOK_INPUT,
            expect_success=False,
        )
        assert result.returncode == 0
        assert "Keep functions small." in result.stdout
        assert "[from: alice]" in result.stdout
        assert "Always write tests." in result.stdout

    def test_retrieve_includes_published_guidelines(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        own_dir = evolve_dir / "entities" / "guideline"
        own_dir.mkdir(parents=True)
        (own_dir / "guideline.md").write_text("---\ntype: guideline\n---\n\nKeep functions small.\n")
        # Published entities live inside the write-scope repo's local clone.
        published_dir = evolve_dir / "entities" / "subscribed" / "my-memory" / "guideline"
        published_dir.mkdir(parents=True)
        (published_dir / "published-guideline.md").write_text(
            "---\ntype: guideline\nvisibility: public\nsource: alice/evolve-guidelines\n---\n\nDocument edge cases.\n"
        )

        result = run_script(
            RETRIEVE_SCRIPT,
            project_dir=temp_project_dir,
            evolve_dir=evolve_dir,
            stdin=HOOK_INPUT,
            expect_success=False,
        )
        assert result.returncode == 0
        assert "Keep functions small." in result.stdout
        assert "Document edge cases." in result.stdout


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
    """Pre-create the local clone publish.py expects under entities/subscribed/<repo>/.

    publish.py refuses to run unless the write-scope repo has already been cloned;
    that's normally subscribe/sync's job. In tests we stand it up directly from
    the local_repo bare so the precondition is met.
    """
    clone_dir = project_dir / ".evolve" / "entities" / "subscribed" / repo
    clone_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--branch", "main", str(local_repo["bare"]), str(clone_dir)],
        check=True,
        capture_output=True,
        env=local_repo["env"],
    )
    return clone_dir


class TestCodexSharingScripts:
    def test_publish_moves_entity_to_target_repo_clone_and_audits(self, temp_project_dir, local_repo):
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        source = guideline_dir / "my-guideline.md"
        source.write_text("---\ntype: guideline\n---\n\nPrefer composition over inheritance.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(_WRITE_REPO_CONFIG)
        _clone_write_target(temp_project_dir, local_repo)

        run_script(
            PUBLISH_SCRIPT,
            project_dir=temp_project_dir,
            args=["--entity", "my-guideline.md", "--user", "alice"],
            evolve_dir=temp_project_dir / ".evolve",
        )

        published = _published_path(temp_project_dir, "my-guideline.md")
        assert published.exists()
        assert not source.exists()
        content = published.read_text()
        assert "visibility: public" in content
        assert "owner: alice" in content
        assert "published_at:" in content

        entry = json.loads((temp_project_dir / ".evolve" / "audit.log").read_text().strip())
        assert entry["action"] == "publish"
        assert entry["actor"] == "alice"
        assert entry["repo"] == "evolve-guidelines"

    def test_publish_stamps_source_from_repo_remote(self, temp_project_dir, local_repo):
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        (guideline_dir / "my-guideline.md").write_text("---\ntype: guideline\n---\n\nPrefer composition over inheritance.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(_WRITE_REPO_CONFIG)
        _clone_write_target(temp_project_dir, local_repo)

        run_script(
            PUBLISH_SCRIPT,
            project_dir=temp_project_dir,
            args=["--entity", "my-guideline.md"],
            evolve_dir=temp_project_dir / ".evolve",
        )

        content = _published_path(temp_project_dir, "my-guideline.md").read_text()
        assert "source: alice/evolve-guidelines" in content

    def test_publish_uses_identity_user_as_owner_and_audit_fallback(self, temp_project_dir, local_repo):
        """Without --user, publish falls back to identity.user for owner/actor."""
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        (guideline_dir / "my-guideline.md").write_text("---\ntype: guideline\n---\n\nPrefer composition over inheritance.\n")
        (temp_project_dir / "evolve.config.yaml").write_text('identity:\n  user: "alice"\n' + _WRITE_REPO_CONFIG)
        _clone_write_target(temp_project_dir, local_repo)

        run_script(
            PUBLISH_SCRIPT,
            project_dir=temp_project_dir,
            args=["--entity", "my-guideline.md"],
            evolve_dir=temp_project_dir / ".evolve",
        )

        content = _published_path(temp_project_dir, "my-guideline.md").read_text()
        assert "owner: alice" in content
        # source is derived from the remote when available (not identity.user).
        assert "source: alice/evolve-guidelines" in content
        entry = json.loads((temp_project_dir / ".evolve" / "audit.log").read_text().strip())
        assert entry["actor"] == "alice"

    def test_publish_fails_when_entity_not_found(self, temp_project_dir):
        # No config needed — the entity-not-found check runs before repo selection.
        result = run_script(
            PUBLISH_SCRIPT,
            project_dir=temp_project_dir,
            args=["--entity", "missing.md"],
            evolve_dir=temp_project_dir / ".evolve",
            expect_success=False,
        )
        assert result.returncode != 0
        assert "not found" in result.stderr

    def test_publish_succeeds_without_user_flag(self, temp_project_dir, local_repo):
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        (guideline_dir / "my-guideline.md").write_text("---\ntype: guideline\n---\n\nPrefer composition.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(_WRITE_REPO_CONFIG)
        _clone_write_target(temp_project_dir, local_repo)

        run_script(
            PUBLISH_SCRIPT,
            project_dir=temp_project_dir,
            args=["--entity", "my-guideline.md"],
            evolve_dir=temp_project_dir / ".evolve",
        )
        content = _published_path(temp_project_dir, "my-guideline.md").read_text()
        assert "visibility: public" in content

    def test_publish_fails_when_entity_already_published(self, temp_project_dir, local_repo):
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        source = guideline_dir / "my-guideline.md"
        source.write_text("---\ntype: guideline\n---\n\nPrefer composition.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(_WRITE_REPO_CONFIG)
        _clone_write_target(temp_project_dir, local_repo)

        dest_path = _published_path(temp_project_dir, "my-guideline.md")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        existing_content = "---\ntype: guideline\nvisibility: public\n---\n\nExisting public content.\n"
        dest_path.write_text(existing_content)

        result = run_script(
            PUBLISH_SCRIPT,
            project_dir=temp_project_dir,
            args=["--entity", "my-guideline.md"],
            evolve_dir=temp_project_dir / ".evolve",
            expect_success=False,
        )

        assert result.returncode != 0
        assert "already published" in result.stderr
        assert dest_path.read_text() == existing_content
        assert source.exists()

    def test_publish_errors_without_write_scope_repo(self, temp_project_dir):
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        (guideline_dir / "my-guideline.md").write_text("---\ntype: guideline\n---\n\nContent.\n")

        result = run_script(
            PUBLISH_SCRIPT,
            project_dir=temp_project_dir,
            args=["--entity", "my-guideline.md"],
            evolve_dir=temp_project_dir / ".evolve",
            expect_success=False,
        )
        assert result.returncode != 0
        assert "no write-scope repo" in result.stderr

    @pytest.mark.parametrize("entity_name", ["../../etc/passwd", "subdir/guideline.md", ".", ".."])
    def test_publish_rejects_invalid_entity_name(self, temp_project_dir, entity_name):
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        (guideline_dir / "guideline.md").write_text("---\ntype: guideline\n---\n\nA guideline.\n")
        result = run_script(
            PUBLISH_SCRIPT,
            project_dir=temp_project_dir,
            args=["--entity", entity_name],
            evolve_dir=temp_project_dir / ".evolve",
            expect_success=False,
        )
        assert result.returncode != 0
        assert "invalid entity name" in result.stderr

    def test_subscribe_sync_and_unsubscribe_round_trip(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"

        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        assert (evolve_dir / "entities" / "subscribed" / "alice").is_dir()

        run_script(SYNC_SCRIPT, project_dir=temp_project_dir, evolve_dir=evolve_dir)
        synced = evolve_dir / "entities" / "subscribed" / "alice" / "guideline" / "guideline-one.md"
        assert synced.exists()
        assert "Always write tests." in synced.read_text()

        run_script(
            UNSUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice"],
            evolve_dir=evolve_dir,
        )
        assert not (evolve_dir / "entities" / "subscribed" / "alice").exists()

    def test_subscribe_updates_config_and_rejects_duplicate(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        config_text = (temp_project_dir / "evolve.config.yaml").read_text()
        assert "name: alice" in config_text

        result = run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "already exists" in result.stderr

    def test_subscribe_rolls_back_clone_when_config_save_fails(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        (temp_project_dir / "evolve.config.yaml").mkdir()

        result = run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )

        assert result.returncode != 0
        assert not (evolve_dir / "entities" / "subscribed" / "alice").exists()

    def test_subscribe_warns_when_audit_write_fails(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        (evolve_dir / "audit.log").mkdir(parents=True)

        result = run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )

        assert result.returncode == 0
        assert "Warning: failed to append audit entry for subscribe" in result.stderr
        assert (evolve_dir / "entities" / "subscribed" / "alice").is_dir()
        config_text = (temp_project_dir / "evolve.config.yaml").read_text()
        assert "name: alice" in config_text

    def test_subscribe_rejects_path_traversal_in_name(self, temp_project_dir, local_repo):
        result = run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "../../outside", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=temp_project_dir / ".evolve",
            expect_success=False,
        )
        assert result.returncode != 0
        assert "invalid subscription name" in result.stderr

    @pytest.mark.parametrize("name", ["", "."])
    def test_subscribe_rejects_empty_or_dot_name(self, temp_project_dir, local_repo, name):
        result = run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", name, "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=temp_project_dir / ".evolve",
            expect_success=False,
        )

        assert result.returncode != 0
        assert "invalid subscription name" in result.stderr

    def test_subscribe_fails_when_destination_already_exists(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        existing_dest = evolve_dir / "entities" / "subscribed" / "alice"
        existing_dest.mkdir(parents=True)

        result = run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )

        assert result.returncode != 0
        assert "destination already exists" in result.stderr
        config_path = temp_project_dir / "evolve.config.yaml"
        assert not config_path.exists()

    def test_unsubscribe_list_and_not_found(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        result = run_script(
            UNSUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--list"],
            evolve_dir=evolve_dir,
        )
        data = json.loads(result.stdout)
        assert data[0]["name"] == "alice"

        missing = run_script(
            UNSUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "missing"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert missing.returncode != 0
        assert "not found" in missing.stderr

    def test_unsubscribe_rejects_path_traversal_in_name(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        result = run_script(
            UNSUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "../../outside"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode != 0
        assert "invalid subscription name" in result.stderr

    def test_sync_quiet_exits_cleanly_without_changes(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        run_script(SYNC_SCRIPT, project_dir=temp_project_dir, evolve_dir=evolve_dir)
        result = run_script(
            SYNC_SCRIPT,
            project_dir=temp_project_dir,
            args=["--quiet"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_sync_no_subscriptions_exits_cleanly(self, temp_project_dir):
        result = run_script(
            SYNC_SCRIPT,
            project_dir=temp_project_dir,
            evolve_dir=temp_project_dir / ".evolve",
            expect_success=False,
        )
        assert result.returncode == 0
        assert "No subscriptions" in result.stdout
        assert "evolve-lite:subscribe" in result.stdout

    def test_sync_skips_invalid_subscription_name(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        (temp_project_dir / "evolve.config.yaml").write_text(
            'repos:\n  - name: "."\n    scope: "read"\n    remote: "https://example.com/repo.git"\n    branch: "main"\n'
        )

        result = run_script(
            SYNC_SCRIPT,
            project_dir=temp_project_dir,
            evolve_dir=evolve_dir,
            expect_success=False,
        )

        assert result.returncode == 0
        assert "'.' (skipped - invalid subscription name)" in result.stdout
        assert not (evolve_dir / "entities" / "subscribed").exists()

    def test_sync_uses_workspace_config_with_custom_evolve_dir(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / "custom-evolve"
        subscribed_dir = evolve_dir / "entities" / "subscribed"
        subscribed_dir.mkdir(parents=True)
        subprocess.run(
            ["git", "clone", "--branch", "main", "--depth", "1", str(local_repo["bare"]), str(subscribed_dir / "alice")],
            check=True,
            env=local_repo["env"],
        )
        (temp_project_dir / "evolve.config.yaml").write_text(
            f'identity:\n  user: "alice"\n'
            f'repos:\n  - name: "alice"\n    scope: "read"\n'
            f'    remote: "{local_repo["bare"]}"\n    branch: "main"\n'
        )

        result = run_script(
            SYNC_SCRIPT,
            project_dir=temp_project_dir,
            evolve_dir=evolve_dir,
            expect_success=False,
        )

        assert result.returncode == 0
        mirrored = evolve_dir / "entities" / "subscribed" / "alice" / "guideline" / "guideline-one.md"
        assert mirrored.exists()
        audit_log = evolve_dir / ".evolve" / "audit.log"
        assert audit_log.exists()
        entry = json.loads(audit_log.read_text().strip())
        assert entry["action"] == "sync"
        assert entry["actor"] == "alice"

    def test_manual_sync_runs_even_when_on_session_start_is_false(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        (temp_project_dir / "evolve.config.yaml").write_text(
            f'repos:\n  - name: "alice"\n    scope: "read"\n'
            f'    remote: "{local_repo["bare"]}"\n    branch: "main"\n'
            f"sync:\n  on_session_start: false\n"
        )

        result = run_script(
            SYNC_SCRIPT,
            project_dir=temp_project_dir,
            evolve_dir=evolve_dir,
            expect_success=False,
        )

        assert result.returncode == 0
        assert "Synced 1 repo(s):" in result.stdout

    def test_session_start_sync_respects_on_session_start_false(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        git_env = local_repo["env"]
        new_entity = local_repo["work"] / "guideline" / "guideline-two.md"
        new_entity.write_text("---\ntype: guideline\n---\n\nDelete dead code promptly.\n")
        subprocess.run(["git", "-C", str(local_repo["work"]), "add", "."], check=True, env=git_env)
        subprocess.run(["git", "-C", str(local_repo["work"]), "commit", "-m", "add guideline-two"], check=True, env=git_env)
        subprocess.run(["git", "-C", str(local_repo["work"]), "push", "origin", "main"], check=True, env=git_env)
        (temp_project_dir / "evolve.config.yaml").write_text(
            f'repos:\n  - name: "alice"\n    scope: "read"\n'
            f'    remote: "{local_repo["bare"]}"\n    branch: "main"\n'
            f"sync:\n  on_session_start: false\n"
        )

        result = run_script(
            SYNC_SCRIPT,
            project_dir=temp_project_dir,
            args=["--quiet", "--session-start"],
            evolve_dir=evolve_dir,
            expect_success=False,
        )

        assert result.returncode == 0
        synced = evolve_dir / "entities" / "subscribed" / "alice" / "guideline" / "guideline-two.md"
        assert not synced.exists()

    def test_sync_writes_audit_log(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        run_script(SYNC_SCRIPT, project_dir=temp_project_dir, evolve_dir=evolve_dir)
        actions = [
            json.loads(line)["action"] for line in (temp_project_dir / ".evolve" / "audit.log").read_text().splitlines() if line.strip()
        ]
        assert "sync" in actions

    def test_sync_picks_up_new_entity_after_push(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        run_script(SYNC_SCRIPT, project_dir=temp_project_dir, evolve_dir=evolve_dir)

        git_env = local_repo["env"]
        new_entity = local_repo["work"] / "guideline" / "guideline-two.md"
        new_entity.write_text("---\ntype: guideline\n---\n\nDelete dead code promptly.\n")
        subprocess.run(["git", "-C", str(local_repo["work"]), "add", "."], check=True, env=git_env)
        subprocess.run(["git", "-C", str(local_repo["work"]), "commit", "-m", "add guideline-two"], check=True, env=git_env)
        subprocess.run(["git", "-C", str(local_repo["work"]), "push", "origin", "main"], check=True, env=git_env)

        run_script(SYNC_SCRIPT, project_dir=temp_project_dir, evolve_dir=evolve_dir)
        mirrored = evolve_dir / "entities" / "subscribed" / "alice" / "guideline" / "guideline-two.md"
        assert mirrored.exists()
        assert "Delete dead code promptly." in mirrored.read_text()

    def test_sync_skips_symlinked_markdown_files(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )

        git_env = local_repo["env"]
        target = local_repo["work"] / "guideline" / "guideline-one.md"
        symlink = local_repo["work"] / "guideline" / "guideline-link.md"
        symlink.symlink_to(target.name)
        subprocess.run(["git", "-C", str(local_repo["work"]), "add", "."], check=True, env=git_env)
        subprocess.run(["git", "-C", str(local_repo["work"]), "commit", "-m", "add guideline symlink"], check=True, env=git_env)
        subprocess.run(["git", "-C", str(local_repo["work"]), "push", "origin", "main"], check=True, env=git_env)

        run_script(SYNC_SCRIPT, project_dir=temp_project_dir, evolve_dir=evolve_dir)

        subscribed_dir = evolve_dir / "entities" / "subscribed" / "alice" / "guideline"
        assert (subscribed_dir / "guideline-one.md").exists()
        assert (subscribed_dir / "guideline-link.md").exists()

        result = run_script(
            RETRIEVE_SCRIPT,
            project_dir=temp_project_dir,
            evolve_dir=evolve_dir,
            stdin=HOOK_INPUT,
            expect_success=False,
        )
        assert result.returncode == 0
        assert "Always write tests." in result.stdout
        assert "guideline-link" not in result.stdout

    def test_sync_removed_entity_disappears_after_sync(self, temp_project_dir, local_repo):
        evolve_dir = temp_project_dir / ".evolve"
        run_script(
            SUBSCRIBE_SCRIPT,
            project_dir=temp_project_dir,
            args=["--name", "alice", "--remote", str(local_repo["bare"]), "--branch", "main"],
            evolve_dir=evolve_dir,
        )
        run_script(SYNC_SCRIPT, project_dir=temp_project_dir, evolve_dir=evolve_dir)

        guideline_one = evolve_dir / "entities" / "subscribed" / "alice" / "guideline" / "guideline-one.md"
        assert guideline_one.exists()

        git_env = local_repo["env"]
        subprocess.run(["git", "-C", str(local_repo["work"]), "rm", "guideline/guideline-one.md"], check=True, env=git_env)
        subprocess.run(["git", "-C", str(local_repo["work"]), "commit", "-m", "remove guideline-one"], check=True, env=git_env)
        subprocess.run(["git", "-C", str(local_repo["work"]), "push", "origin", "main"], check=True, env=git_env)

        run_script(SYNC_SCRIPT, project_dir=temp_project_dir, evolve_dir=evolve_dir)
        assert not guideline_one.exists()
