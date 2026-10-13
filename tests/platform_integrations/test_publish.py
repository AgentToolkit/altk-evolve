"""Tests for skills/evolve-lite/publish/scripts/publish.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_REPO_ROOT = Path(__file__).parent.parent.parent
CLAUDE_PUBLISH_SCRIPT = _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite/skills/evolve-lite/publish/scripts/publish.py"
CODEX_PUBLISH_SCRIPT = _REPO_ROOT / "platform-integrations/codex/plugins/evolve-lite/skills/evolve-lite/publish/scripts/publish.py"
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


# ---------------------------------------------------------------------------
# Non-guideline entity types (#373): adapt-memory and the EVOLVE.md save flow
# write .evolve/entities/<native type>/, not guideline/.
# ---------------------------------------------------------------------------

ADAPT_MEMORY_SCRIPT = (
    _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite/skills/evolve-lite/adapt-memory/scripts/adapt_memory.py"
)


def _write_private(project_dir, entity_type, filename, body="Use kilometres, never miles."):
    type_dir = project_dir / ".evolve" / "entities" / entity_type
    type_dir.mkdir(parents=True, exist_ok=True)
    path = type_dir / filename
    path.write_text(f"---\ntype: {entity_type}\n---\n\n{body}\n")
    return path


def _typed_dest(project_dir, entity_type, filename, repo=_DEFAULT_WRITE_REPO):
    return project_dir / ".evolve" / "entities" / "subscribed" / repo / entity_type / filename


@pytest.fixture
def typed_project(temp_project_dir):
    """A project with a write-scope repo configured and no private entities yet."""
    (temp_project_dir / "evolve.config.yaml").write_text(_DEFAULT_CONFIG)
    _init_fake_clone(temp_project_dir, _DEFAULT_WRITE_REPO)
    return temp_project_dir


class TestPublishAnyEntityType:
    def test_publishes_entity_mirrored_by_adapt_memory(self, typed_project, tmp_path):
        """The #373 repro: mirror a native `feedback` memory, then publish it."""
        native = tmp_path / "native" / "canada_km_only.md"
        native.parent.mkdir()
        native.write_text(
            "---\nname: canada-km-only\ndescription: Distances for Canadian users\nmetadata:\n  type: feedback\n---\n\nAlways report distances in km.\n"
        )
        env = {**os.environ, "EVOLVE_DIR": str(typed_project / ".evolve")}
        subprocess.run(
            [sys.executable, str(ADAPT_MEMORY_SCRIPT), str(native), "--trigger", "When reporting a distance"],
            cwd=str(typed_project),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        source = typed_project / ".evolve" / "entities" / "feedback" / "canada-km-only.md"
        assert source.is_file()

        run_publish(typed_project, ["--entity", "canada-km-only.md", "--user", "alice"])

        dest = _typed_dest(typed_project, "feedback", "canada-km-only.md")
        assert dest.is_file()
        assert not source.exists()
        content = dest.read_text()
        assert "Always report distances in km." in content
        assert "visibility: public" in content
        assert "owner: alice" in content
        assert not (typed_project / ".evolve" / "entities" / "subscribed" / _DEFAULT_WRITE_REPO / "guideline").exists()

    @pytest.mark.parametrize("entity_type", ["user", "feedback", "project", "reference", "guideline"])
    @pytest.mark.parametrize(("platform_name", "publish_script"), PUBLISH_SCRIPT_VARIANTS)
    def test_keeps_type_directory_in_shared_repo(self, typed_project, entity_type, publish_script, platform_name):
        source = _write_private(typed_project, entity_type, "note.md")
        run_publish_script(publish_script, typed_project, ["--entity", "note.md"])
        assert _typed_dest(typed_project, entity_type, "note.md").is_file()
        assert not source.exists()

    def test_explicit_type_flag(self, typed_project):
        source = _write_private(typed_project, "reference", "note.md")
        run_publish(typed_project, ["--entity", "note.md", "--type", "reference"])
        assert _typed_dest(typed_project, "reference", "note.md").is_file()
        assert not source.exists()

    def test_ambiguous_name_is_rejected_and_nothing_moves(self, typed_project):
        feedback = _write_private(typed_project, "feedback", "note.md", body="feedback body")
        project = _write_private(typed_project, "project", "note.md", body="project body")

        result = run_publish(typed_project, ["--entity", "note.md"], expect_success=False)

        assert result.returncode != 0
        assert "ambiguous" in result.stderr
        assert "feedback/note.md" in result.stderr and "project/note.md" in result.stderr
        assert feedback.exists() and project.exists()
        assert not (typed_project / ".evolve" / "entities" / "subscribed" / _DEFAULT_WRITE_REPO / "feedback").exists()

    def test_type_flag_resolves_ambiguity(self, typed_project):
        feedback = _write_private(typed_project, "feedback", "note.md", body="feedback body")
        project = _write_private(typed_project, "project", "note.md", body="project body")

        run_publish(typed_project, ["--entity", "note.md", "--type", "project"])

        assert "project body" in _typed_dest(typed_project, "project", "note.md").read_text()
        assert not project.exists()
        assert feedback.exists(), "the other type's entity must be left alone"

    def test_type_flag_pointing_at_wrong_type_is_not_found(self, typed_project):
        source = _write_private(typed_project, "feedback", "note.md")
        result = run_publish(typed_project, ["--entity", "note.md", "--type", "project"], expect_success=False)
        assert result.returncode != 0
        assert "not found" in result.stderr
        assert source.exists()

    def test_never_publishes_from_subscribed(self, typed_project):
        """Entities under entities/subscribed/ belong to other repos; publish must not pick them up."""
        theirs = typed_project / ".evolve" / "entities" / "subscribed" / "note.md"
        theirs.write_text("---\ntype: guideline\n---\n\nSomeone else's.\n")

        result = run_publish(typed_project, ["--entity", "note.md"], expect_success=False)
        assert result.returncode != 0
        assert "not found" in result.stderr
        assert theirs.exists()

        result = run_publish(typed_project, ["--entity", "note.md", "--type", "subscribed"], expect_success=False)
        assert result.returncode != 0
        assert "invalid entity type" in result.stderr
        assert theirs.exists()

    def test_symlinked_type_dir_cannot_reach_into_subscribed(self, typed_project):
        """A type directory symlinked into a subscribed clone must not become a publish source."""
        read_clone = typed_project / ".evolve" / "entities" / "subscribed" / "read" / "feedback"
        read_clone.mkdir(parents=True)
        theirs = read_clone / "note.md"
        theirs.write_text("---\ntype: feedback\n---\n\nSomeone else's.\n")
        (typed_project / ".evolve" / "entities" / "feedback").symlink_to(read_clone, target_is_directory=True)

        for args in (["--entity", "note.md"], ["--entity", "note.md", "--type", "feedback"]):
            result = run_publish(typed_project, args, expect_success=False)
            assert result.returncode != 0
            assert "not found" in result.stderr
        assert theirs.exists()
        assert not _typed_dest(typed_project, "feedback", "note.md").exists()

    def test_symlinked_type_dir_outside_subscribed_still_publishes(self, typed_project, tmp_path):
        """Symlinking a type directory elsewhere (e.g. a shared drive) is a legitimate layout."""
        elsewhere = tmp_path / "elsewhere" / "feedback"
        elsewhere.mkdir(parents=True)
        source = elsewhere / "note.md"
        source.write_text("---\ntype: feedback\n---\n\nMine.\n")
        (typed_project / ".evolve" / "entities" / "feedback").symlink_to(elsewhere, target_is_directory=True)

        run_publish(typed_project, ["--entity", "note.md"])
        assert _typed_dest(typed_project, "feedback", "note.md").is_file()
        assert not source.exists()

    def test_audit_log_records_type(self, typed_project):
        _write_private(typed_project, "feedback", "note.md")
        run_publish(typed_project, ["--entity", "note.md", "--user", "alice"])
        entry = json.loads((typed_project / ".evolve" / "audit.log").read_text().strip())
        assert entry["entity"] == "note.md"
        assert entry["type"] == "feedback"
