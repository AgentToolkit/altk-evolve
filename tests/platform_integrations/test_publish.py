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


def run_publish_default_env(project_dir, args, expect_success=True):
    """Run publish the way a real install does: EVOLVE_DIR unset, so it resolves `.evolve` from cwd."""
    env = {k: v for k, v in os.environ.items() if k != "EVOLVE_DIR"}
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

    def test_name_collision_with_another_owner_names_them(self, project_dir):
        """Two teammates' same-named entities: don't tell the second one to delete the first's."""
        dest = _dest_path(project_dir, "my-guideline.md")
        dest.parent.mkdir(parents=True)
        theirs = "---\ntype: guideline\nowner: alice\nvisibility: public\n---\n\nAlice's guideline.\n"
        dest.write_text(theirs)
        source = project_dir / ".evolve" / "entities" / "guideline" / "my-guideline.md"

        result = run_publish(project_dir, ["--entity", "my-guideline.md", "--user", "carol"], expect_success=False)

        assert result.returncode != 0
        assert "name collision" in result.stderr
        assert "alice" in result.stderr
        assert "Rename your memory or entity" in result.stderr
        assert "Unpublish it first" not in result.stderr
        assert dest.read_text() == theirs
        assert source.exists()

    @pytest.mark.parametrize("existing_owner", ["carol", None], ids=["same-owner", "no-owner"])
    def test_already_published_by_same_or_unknown_owner_keeps_unpublish_hint(self, project_dir, existing_owner):
        dest = _dest_path(project_dir, "my-guideline.md")
        dest.parent.mkdir(parents=True)
        owner_line = f"owner: {existing_owner}\n" if existing_owner else ""
        existing = f"---\ntype: guideline\n{owner_line}visibility: public\n---\n\nEarlier copy.\n"
        dest.write_text(existing)

        result = run_publish(project_dir, ["--entity", "my-guideline.md", "--user", "carol"], expect_success=False)

        assert result.returncode != 0
        assert "already published" in result.stderr
        assert "Unpublish it first or delete it manually." in result.stderr
        assert "name collision" not in result.stderr
        assert dest.read_text() == existing

    @pytest.mark.parametrize("where", ["outside", "inside"])
    def test_refuses_a_symlinked_type_dir_in_the_clone(self, project_dir, tmp_path, where):
        """A repo member can commit `guideline -> ../../../../outside` into the shared repo;
        publish must not follow it, whether it points out of the clone or elsewhere in it."""
        clone = project_dir / ".evolve" / "entities" / "subscribed" / _DEFAULT_WRITE_REPO
        if where == "outside":
            target = tmp_path / "outside"
        else:
            target = clone / "elsewhere"
        target.mkdir()
        (clone / "guideline").symlink_to(target, target_is_directory=True)
        source = project_dir / ".evolve" / "entities" / "guideline" / "my-guideline.md"

        result = run_publish(project_dir, ["--entity", "my-guideline.md"], expect_success=False)

        assert result.returncode != 0
        assert "symlink" in result.stderr
        assert list(target.iterdir()) == []
        assert source.exists()

    def test_publishes_into_a_write_clone_that_is_itself_a_symlink(self, project_dir, tmp_path):
        """The clone root may live elsewhere (e.g. a shared checkout); only its type dirs are checked."""
        subscribed = project_dir / ".evolve" / "entities" / "subscribed"
        real_clone = tmp_path / "real-clone"
        (subscribed / _DEFAULT_WRITE_REPO / ".git").rename(tmp_path / "git-dir")
        (subscribed / _DEFAULT_WRITE_REPO).rmdir()
        real_clone.mkdir()
        (tmp_path / "git-dir").rename(real_clone / ".git")
        (subscribed / _DEFAULT_WRITE_REPO).symlink_to(real_clone, target_is_directory=True)

        run_publish(project_dir, ["--entity", "my-guideline.md"])

        assert (real_clone / "guideline" / "my-guideline.md").is_file()

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions")
    def test_unremovable_source_is_refused_before_anything_is_written(self, project_dir):
        """If the private copy can't be removed, publish must fail up front, not leave two copies."""
        type_dir = project_dir / ".evolve" / "entities" / "guideline"
        source = type_dir / "my-guideline.md"
        type_dir.chmod(0o555)
        try:
            result = run_publish(project_dir, ["--entity", "my-guideline.md"], expect_success=False)
        finally:
            type_dir.chmod(0o755)

        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert "not writable" in result.stderr
        assert source.exists()
        assert not _dest_path(project_dir, "my-guideline.md").parent.exists()
        assert not (project_dir / ".evolve" / "audit.log").exists()

    def test_source_unlink_failure_after_publishing_is_reported_and_audited(self, project_dir):
        """If removing the private copy still fails after the write, keep the published copy,
        say which private copy is left over, and record the publish in the audit log."""
        source = project_dir / ".evolve" / "entities" / "guideline" / "my-guideline.md"
        wrapper = (
            "import pathlib, runpy, sys\n"
            "_unlink = pathlib.Path.unlink\n"
            "def unlink(self, *a, **k):\n"
            "    if self.name == 'my-guideline.md' and self.parent.name == 'guideline' and 'subscribed' not in self.parts:\n"
            "        raise PermissionError(13, 'Permission denied', str(self))\n"
            "    return _unlink(self, *a, **k)\n"
            "pathlib.Path.unlink = unlink\n"
            "sys.argv = sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
        env = {**os.environ, "EVOLVE_DIR": str(project_dir / ".evolve")}
        result = subprocess.run(
            [sys.executable, "-c", wrapper, str(PUBLISH_SCRIPT), "--entity", "my-guideline.md", "--user", "alice"],
            capture_output=True,
            text=True,
            cwd=str(project_dir),
            env=env,
        )

        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr
        assert "could not remove the private copy" in result.stderr
        assert str(source.resolve()) in result.stderr
        assert _dest_path(project_dir, "my-guideline.md").is_file()
        assert source.exists()
        entry = json.loads((project_dir / ".evolve" / "audit.log").read_text().strip())
        assert entry["action"] == "publish"
        assert entry["entity"] == "my-guideline.md"
        assert entry["source_removed"] is False

    def _write_with_trajectory(self, project_dir, trajectory):
        source = project_dir / ".evolve" / "entities" / "guideline" / "my-guideline.md"
        source.write_text(f"---\ntype: guideline\ntrajectory: {trajectory}\n---\n\nPrefer composition.\n")

    def test_absolute_trajectory_inside_the_project_is_published_relative(self, project_dir):
        """The manual learn flow stores save_trajectory's absolute path; it must not reach the shared repo."""
        absolute = project_dir.resolve() / ".evolve" / "trajectories" / "trajectory_2026-10-16_abc.json"
        self._write_with_trajectory(project_dir, absolute)

        run_publish(project_dir, ["--entity", "my-guideline.md"])

        content = _dest_path(project_dir, "my-guideline.md").read_text()
        assert "trajectory: .evolve/trajectories/trajectory_2026-10-16_abc.json" in content
        assert str(project_dir.resolve()) not in content

    def test_absolute_trajectory_outside_the_project_is_dropped(self, project_dir, tmp_path):
        absolute = tmp_path / "elsewhere" / "trajectories" / "trajectory_2026-10-16_abc.json"
        self._write_with_trajectory(project_dir, absolute)

        run_publish(project_dir, ["--entity", "my-guideline.md"])

        content = _dest_path(project_dir, "my-guideline.md").read_text()
        assert "trajectory" not in content
        assert str(tmp_path) not in content

    def test_relative_trajectory_is_published_unchanged(self, project_dir):
        """The stop-hook flow already writes a project-relative path."""
        self._write_with_trajectory(project_dir, ".evolve/trajectories/claude-transcript_abc.jsonl")

        run_publish(project_dir, ["--entity", "my-guideline.md"])

        content = _dest_path(project_dir, "my-guideline.md").read_text()
        assert "trajectory: .evolve/trajectories/claude-transcript_abc.jsonl" in content

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
        # The publisher's local path to the native memory stays local.
        assert "native_path" not in content
        assert str(native.parent) not in content
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

    def test_symlinked_type_dir_cannot_reach_into_subscribed_with_default_evolve_dir(self, typed_project):
        """Same layout as above, but with EVOLVE_DIR unset, so `.evolve` is relative to cwd."""
        read_clone = typed_project / ".evolve" / "entities" / "subscribed" / "read" / "feedback"
        read_clone.mkdir(parents=True)
        theirs = read_clone / "note.md"
        theirs.write_text("---\ntype: feedback\n---\n\nSomeone else's.\n")
        (typed_project / ".evolve" / "entities" / "feedback").symlink_to(read_clone, target_is_directory=True)

        result = run_publish_default_env(typed_project, ["--entity", "note.md"], expect_success=False)

        assert result.returncode != 0
        assert "not found" in result.stderr
        assert theirs.exists()

    def test_symlinked_clone_dir_is_still_protected(self, typed_project, tmp_path):
        """A subscribed clone that is itself a symlink to somewhere else is still a subscribed clone."""
        real_clone = tmp_path / "clones" / "read"
        (real_clone / "feedback").mkdir(parents=True)
        theirs = real_clone / "feedback" / "note.md"
        theirs.write_text("---\ntype: feedback\n---\n\nSomeone else's.\n")
        subscribed = typed_project / ".evolve" / "entities" / "subscribed"
        (subscribed / "read").symlink_to(real_clone, target_is_directory=True)
        (typed_project / ".evolve" / "entities" / "feedback").symlink_to(Path("subscribed") / "read" / "feedback", target_is_directory=True)

        for run in (run_publish, run_publish_default_env):
            result = run(typed_project, ["--entity", "note.md"], expect_success=False)
            assert result.returncode != 0
            assert "not found" in result.stderr
        assert theirs.exists()
        assert not _typed_dest(typed_project, "feedback", "note.md").exists()

    def test_symlink_with_differently_cased_target_cannot_reach_into_subscribed(self, typed_project):
        """On a case-insensitive filesystem `SUBSCRIBED/read/feedback` is the subscribed clone, but
        `resolve()` keeps the spelling, so a string-prefix check would miss it. Identity must catch it."""
        probe = typed_project / ".case-probe"
        probe.write_text("")
        if not (typed_project / ".CASE-PROBE").exists():
            pytest.skip("filesystem is case-sensitive")
        read_clone = typed_project / ".evolve" / "entities" / "subscribed" / "read" / "feedback"
        read_clone.mkdir(parents=True)
        theirs = read_clone / "note.md"
        theirs.write_text("---\ntype: feedback\n---\n\nSomeone else's.\n")
        (typed_project / ".evolve" / "entities" / "feedback").symlink_to(Path("SUBSCRIBED") / "read" / "feedback", target_is_directory=True)

        for run in (run_publish, run_publish_default_env):
            for args in (["--entity", "note.md"], ["--entity", "note.md", "--type", "feedback"]):
                result = run(typed_project, args, expect_success=False)
                assert result.returncode != 0
                assert "not found" in result.stderr
        assert theirs.exists()
        assert not _typed_dest(typed_project, "feedback", "note.md").exists()

    def test_type_flag_selects_a_dir_whose_name_is_not_sanitized(self, typed_project):
        """`--type` matches existing dirs by their sanitized name, so hand-made dirs stay selectable."""
        mine = _write_private(typed_project, "my_notes", "dup.md", body="my notes")
        other = _write_private(typed_project, "guideline", "dup.md", body="a guideline")

        run_publish(typed_project, ["--entity", "dup.md", "--type", "my_notes"])

        assert not mine.exists()
        assert other.exists()
        assert "my notes" in _typed_dest(typed_project, "my_notes", "dup.md").read_text()

    @pytest.mark.parametrize(("picked", "other"), [("my_notes", "my-notes"), ("my-notes", "my_notes")])
    def test_type_flag_prefers_the_exact_dir_name_over_sanitized_matches(self, typed_project, picked, other):
        """`my_notes` and `my-notes` both sanitize to `my-notes`; `--type` must still pick the one named."""
        chosen = _write_private(typed_project, picked, "x.md", body=f"from {picked}")
        left = _write_private(typed_project, other, "x.md", body=f"from {other}")

        run_publish(typed_project, ["--entity", "x.md", "--type", picked])

        assert not chosen.exists()
        assert left.exists(), "the other dir's entity must be left alone"
        assert f"from {picked}" in _typed_dest(typed_project, picked, "x.md").read_text()
        assert not _typed_dest(typed_project, other, "x.md").exists()

    def test_dirs_that_sanitize_alike_are_still_ambiguous_without_type(self, typed_project):
        underscore = _write_private(typed_project, "my_notes", "x.md")
        hyphen = _write_private(typed_project, "my-notes", "x.md")

        for args in (["--entity", "x.md"], ["--entity", "x.md", "--type", "My_Notes"]):
            result = run_publish(typed_project, args, expect_success=False)
            assert result.returncode != 0
            assert "ambiguous" in result.stderr
            assert "my-notes/x.md" in result.stderr and "my_notes/x.md" in result.stderr
        assert underscore.exists() and hyphen.exists()

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

    @pytest.mark.parametrize("type_args", [[], ["--type", "feedback"]], ids=["no-type", "with-type"])
    @pytest.mark.parametrize(("platform_name", "publish_script"), PUBLISH_SCRIPT_VARIANTS)
    def test_symlinked_entity_file_pointing_outside_its_type_dir_is_refused(self, typed_project, publish_script, platform_name, type_args):
        """An entity *file* symlinked out of its type dir must not be published: publish
        unlinks the resolved source, so this would delete data outside `.evolve`."""
        outside = typed_project / "outside" / "native.md"
        outside.parent.mkdir()
        original = "---\ntype: feedback\n---\n\nNot an entity of this project.\n"
        outside.write_text(original)
        type_dir = typed_project / ".evolve" / "entities" / "feedback"
        type_dir.mkdir(parents=True)
        (type_dir / "note.md").symlink_to(Path("..") / ".." / ".." / "outside" / "native.md")
        clone = typed_project / ".evolve" / "entities" / "subscribed" / _DEFAULT_WRITE_REPO

        result = run_publish_script(publish_script, typed_project, ["--entity", "note.md", *type_args], expect_success=False)

        assert result.returncode != 0
        assert "not found" in result.stderr
        assert outside.read_text() == original
        assert (type_dir / "note.md").is_symlink()
        assert sorted(p.name for p in clone.iterdir()) == [".git"]

    @pytest.mark.parametrize("bad_type", ["..", ".", "../..", "", "/"])
    def test_type_flag_cannot_escape_the_entities_dir(self, typed_project, bad_type):
        """--type is a directory name under entities/, never a path: `--type ..` must not
        reach .evolve/ itself (where audit.log and the config live)."""
        audit = typed_project / ".evolve" / "audit.log"
        audit.write_text('{"action": "seed"}\n')
        # Reachable only through an empty type (entities/ itself), so this checks `not entity_type`.
        loose = typed_project / ".evolve" / "entities" / "audit.log"
        loose.write_text("loose\n")

        result = run_publish(typed_project, ["--entity", "audit.log", "--type", bad_type], expect_success=False)

        assert result.returncode != 0
        assert audit.read_text() == '{"action": "seed"}\n'
        assert loose.read_text() == "loose\n"
        assert not list((typed_project / ".evolve" / "entities" / "subscribed" / _DEFAULT_WRITE_REPO).rglob("audit.log"))

    def test_hidden_type_dirs_are_not_searched(self, typed_project):
        """Dot-directories under entities/ (editor or tool state) are never a publish source."""
        hidden = _write_private(typed_project, ".cache", "note.md")

        result = run_publish(typed_project, ["--entity", "note.md"], expect_success=False)

        assert result.returncode != 0
        assert "not found" in result.stderr
        assert hidden.exists()

    def test_audit_log_records_type(self, typed_project):
        _write_private(typed_project, "feedback", "note.md")
        run_publish(typed_project, ["--entity", "note.md", "--user", "alice"])
        entry = json.loads((typed_project / ".evolve" / "audit.log").read_text().strip())
        assert entry["entity"] == "note.md"
        assert entry["type"] == "feedback"
