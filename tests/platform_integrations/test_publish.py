"""Tests for skills/publish/scripts/publish.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_REPO_ROOT = Path(__file__).parent.parent.parent
CLAUDE_PUBLISH_SCRIPT = _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite/skills/publish/scripts/publish.py"
CODEX_PUBLISH_SCRIPT = _REPO_ROOT / "platform-integrations/codex/plugins/evolve-lite/skills/publish/scripts/publish.py"
PUBLISH_SCRIPT = CLAUDE_PUBLISH_SCRIPT
PUBLISH_SCRIPT_VARIANTS = [
    ("claude", CLAUDE_PUBLISH_SCRIPT),
    ("codex", CODEX_PUBLISH_SCRIPT),
]

# All publish flows require a configured write-scope repo. Tests that need a
# valid config use this constant.
_DEFAULT_WRITE_REPO = "memory"
_DEFAULT_CONFIG = (
    "repos:\n"
    f'  - name: "{_DEFAULT_WRITE_REPO}"\n'
    '    scope: "write"\n'
    '    remote: "git@github.com:alice/evolve-guidelines.git"\n'
    '    branch: "main"\n'
)


def _init_fake_clone(project_dir, repo_name):
    """Create the bare-minimum directory shape the publish script expects for a clone."""
    clone_root = project_dir / ".evolve" / "entities" / "subscribed" / repo_name
    (clone_root / ".git").mkdir(parents=True, exist_ok=True)


@pytest.fixture
def project_dir(temp_project_dir):
    """A temp project with one private guideline entity and a write-scope repo configured."""
    guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
    guideline_dir.mkdir(parents=True)
    (guideline_dir / "my-guideline.md").write_text("---\ntype: guideline\n---\n\nPrefer composition over inheritance.\n")
    (temp_project_dir / "evolve.config.yaml").write_text(_DEFAULT_CONFIG)
    _init_fake_clone(temp_project_dir, _DEFAULT_WRITE_REPO)
    return temp_project_dir


def run_publish(project_dir, args, expect_success=True):
    env = {**os.environ, "EVOLVE_DIR": str(project_dir / ".evolve")}
    return subprocess.run(
        [sys.executable, str(PUBLISH_SCRIPT)] + args,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=expect_success,
    )


def run_publish_script(script_path, project_dir, args, expect_success=True):
    env = {**os.environ, "EVOLVE_DIR": str(project_dir / ".evolve")}
    return subprocess.run(
        [sys.executable, str(script_path)] + args,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=expect_success,
    )


def _dest_path(project_dir, filename, repo=_DEFAULT_WRITE_REPO):
    return project_dir / ".evolve" / "entities" / "subscribed" / repo / "guideline" / filename


class TestPublish:
    def test_moves_entity_to_target_repo_clone(self, project_dir):
        source = project_dir / ".evolve" / "entities" / "guideline" / "my-guideline.md"
        run_publish(project_dir, ["--entity", "my-guideline.md"])
        assert _dest_path(project_dir, "my-guideline.md").exists()
        assert not source.exists()

    def test_sets_visibility_public(self, project_dir):
        run_publish(project_dir, ["--entity", "my-guideline.md"])
        content = _dest_path(project_dir, "my-guideline.md").read_text()
        assert "visibility: public" in content

    def test_stamps_published_at_timestamp(self, project_dir):
        run_publish(project_dir, ["--entity", "my-guideline.md"])
        content = _dest_path(project_dir, "my-guideline.md").read_text()
        assert "published_at:" in content

    def test_stamps_owner_when_user_flag_given(self, project_dir):
        run_publish(project_dir, ["--entity", "my-guideline.md", "--user", "alice"])
        content = _dest_path(project_dir, "my-guideline.md").read_text()
        assert "owner: alice" in content

    def test_stamps_source_from_remote_when_resolvable(self, project_dir):
        run_publish(project_dir, ["--entity", "my-guideline.md", "--user", "alice"])
        content = _dest_path(project_dir, "my-guideline.md").read_text()
        # Remote in _DEFAULT_CONFIG is alice/evolve-guidelines — stamped as-is.
        assert "source: alice/evolve-guidelines" in content

    def test_preserves_original_content(self, project_dir):
        run_publish(project_dir, ["--entity", "my-guideline.md"])
        content = _dest_path(project_dir, "my-guideline.md").read_text()
        assert "Prefer composition over inheritance." in content

    def test_writes_audit_log(self, project_dir):
        run_publish(project_dir, ["--entity", "my-guideline.md", "--user", "alice"])
        log_path = project_dir / ".evolve" / "audit.log"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["action"] == "publish"
        assert entry["actor"] == "alice"
        assert entry["entity"] == "my-guideline.md"
        assert entry["repo"] == _DEFAULT_WRITE_REPO

    def test_exits_nonzero_when_entity_not_found(self, project_dir):
        result = run_publish(project_dir, ["--entity", "nonexistent.md"], expect_success=False)
        assert result.returncode != 0
        assert "not found" in result.stderr

    def test_succeeds_without_user_flag(self, project_dir):
        run_publish(project_dir, ["--entity", "my-guideline.md"])
        content = _dest_path(project_dir, "my-guideline.md").read_text()
        assert "visibility: public" in content

    def test_exits_nonzero_when_already_published(self, project_dir):
        dest = _dest_path(project_dir, "my-guideline.md")
        dest.parent.mkdir(parents=True)
        dest.write_text("---\ntype: guideline\nvisibility: public\n---\n\nExisting public content.\n")

        result = run_publish(project_dir, ["--entity", "my-guideline.md"], expect_success=False)

        assert result.returncode != 0
        assert "already published" in result.stderr
        assert dest.read_text() == "---\ntype: guideline\nvisibility: public\n---\n\nExisting public content.\n"
        assert (project_dir / ".evolve" / "entities" / "guideline" / "my-guideline.md").exists()

    def test_rejects_path_traversal_in_entity_name(self, project_dir):
        result = run_publish(project_dir, ["--entity", "../../etc/passwd"], expect_success=False)
        assert result.returncode != 0
        assert "invalid entity name" in result.stderr

    def test_errors_when_no_write_repo_configured(self, temp_project_dir):
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        (guideline_dir / "my-guideline.md").write_text("---\ntype: guideline\n---\n\nContent.\n")
        # No config file at all → no write-scope repo available.
        result = run_publish(temp_project_dir, ["--entity", "my-guideline.md"], expect_success=False)
        assert result.returncode != 0
        assert "no write-scope repo" in result.stderr

    def test_errors_when_repo_flag_points_to_read_scope(self, temp_project_dir):
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        (guideline_dir / "my-guideline.md").write_text("---\ntype: guideline\n---\n\nContent.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(
            'repos:\n  - name: "readonly"\n    scope: "read"\n    remote: "git@github.com:bob/evolve.git"\n    branch: "main"\n'
        )
        result = run_publish(temp_project_dir, ["--entity", "my-guideline.md", "--repo", "readonly"], expect_success=False)
        assert result.returncode != 0
        assert "requires scope=write" in result.stderr

    def test_errors_when_multiple_write_repos_and_no_flag(self, temp_project_dir):
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        (guideline_dir / "my-guideline.md").write_text("---\ntype: guideline\n---\n\nContent.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(
            "repos:\n"
            '  - name: "memory"\n    scope: "write"\n'
            '    remote: "git@github.com:alice/memory.git"\n    branch: "main"\n'
            '  - name: "work"\n    scope: "write"\n'
            '    remote: "git@github.com:acme/work.git"\n    branch: "main"\n'
        )
        result = run_publish(temp_project_dir, ["--entity", "my-guideline.md"], expect_success=False)
        assert result.returncode != 0
        assert "multiple write-scope repos" in result.stderr
        assert "memory" in result.stderr and "work" in result.stderr

    def test_selects_named_repo_when_multiple_write_repos_exist(self, temp_project_dir):
        guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        (guideline_dir / "my-guideline.md").write_text("---\ntype: guideline\n---\n\nContent.\n")
        (temp_project_dir / "evolve.config.yaml").write_text(
            "repos:\n"
            '  - name: "memory"\n    scope: "write"\n'
            '    remote: "git@github.com:alice/memory.git"\n    branch: "main"\n'
            '  - name: "work"\n    scope: "write"\n'
            '    remote: "git@github.com:acme/work.git"\n    branch: "main"\n'
        )
        _init_fake_clone(temp_project_dir, "memory")
        _init_fake_clone(temp_project_dir, "work")
        run_publish(temp_project_dir, ["--entity", "my-guideline.md", "--repo", "work"])
        assert _dest_path(temp_project_dir, "my-guideline.md", repo="work").exists()
        assert not _dest_path(temp_project_dir, "my-guideline.md", repo="memory").exists()


@pytest.mark.parametrize(("platform_name", "publish_script"), PUBLISH_SCRIPT_VARIANTS)
def test_publish_errors_when_target_clone_missing(temp_project_dir, publish_script, platform_name):
    """Publish must refuse to move the source entity if the target clone isn't a git repo."""
    guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
    guideline_dir.mkdir(parents=True)
    source = guideline_dir / "my-guideline.md"
    source.write_text("---\ntype: guideline\n---\n\nContent.\n")
    (temp_project_dir / "evolve.config.yaml").write_text(_DEFAULT_CONFIG)
    # Note: no .git dir created under .evolve/entities/subscribed/memory/.

    result = run_publish_script(publish_script, temp_project_dir, ["--entity", "my-guideline.md"], expect_success=False)
    assert result.returncode != 0
    assert "clone not found" in result.stderr
    # Source entity must still be intact — no data loss.
    assert source.exists()


@pytest.mark.parametrize(("platform_name", "publish_script"), PUBLISH_SCRIPT_VARIANTS)
def test_publish_rejects_directory_entity_path(temp_project_dir, publish_script, platform_name):
    guideline_dir = temp_project_dir / ".evolve" / "entities" / "guideline"
    entity_dir = guideline_dir / "my-guideline.md"
    entity_dir.mkdir(parents=True)

    result = run_publish_script(publish_script, temp_project_dir, ["--entity", "my-guideline.md"], expect_success=False)
    assert result.returncode != 0
    assert "not found or is a directory" in result.stderr
    assert entity_dir.is_dir()
