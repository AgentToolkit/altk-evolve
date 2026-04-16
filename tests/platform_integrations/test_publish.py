"""Tests for skills/publish/scripts/publish.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_PLUGIN_ROOT = (
    Path(__file__).parent.parent.parent
    / "platform-integrations/claude/plugins/evolve-lite"
)
PUBLISH_SCRIPT = _PLUGIN_ROOT / "skills/publish/scripts/publish.py"


@pytest.fixture
def project_dir(tmp_path):
    """A temp project with one private guideline entity."""
    guideline_dir = tmp_path / ".evolve" / "entities" / "guideline"
    guideline_dir.mkdir(parents=True)
    (guideline_dir / "my-tip.md").write_text(
        "---\ntype: guideline\n---\n\nPrefer composition over inheritance.\n"
    )
    return tmp_path


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


class TestPublish:
    def test_copies_entity_to_public_dir(self, project_dir):
        run_publish(project_dir, ["--entity", "my-tip.md"])
        assert (project_dir / ".evolve" / "public" / "guideline" / "my-tip.md").exists()

    def test_sets_visibility_public(self, project_dir):
        run_publish(project_dir, ["--entity", "my-tip.md"])
        content = (project_dir / ".evolve" / "public" / "guideline" / "my-tip.md").read_text()
        assert "visibility: public" in content

    def test_stamps_published_at_timestamp(self, project_dir):
        run_publish(project_dir, ["--entity", "my-tip.md"])
        content = (project_dir / ".evolve" / "public" / "guideline" / "my-tip.md").read_text()
        assert "published_at:" in content

    def test_stamps_owner_when_user_flag_given(self, project_dir):
        run_publish(project_dir, ["--entity", "my-tip.md", "--user", "alice"])
        content = (project_dir / ".evolve" / "public" / "guideline" / "my-tip.md").read_text()
        assert "owner: alice" in content

    def test_preserves_original_content(self, project_dir):
        run_publish(project_dir, ["--entity", "my-tip.md"])
        content = (project_dir / ".evolve" / "public" / "guideline" / "my-tip.md").read_text()
        assert "Prefer composition over inheritance." in content

    def test_writes_audit_log(self, project_dir):
        run_publish(project_dir, ["--entity", "my-tip.md", "--user", "alice"])
        log_path = project_dir / ".evolve" / "audit.log"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["action"] == "publish"
        assert entry["actor"] == "alice"
        assert entry["entity"] == "my-tip.md"

    def test_exits_nonzero_when_entity_not_found(self, project_dir):
        result = run_publish(project_dir, ["--entity", "nonexistent.md"], expect_success=False)
        assert result.returncode != 0
        assert "not found" in result.stderr

    def test_succeeds_without_user_flag(self, project_dir):
        run_publish(project_dir, ["--entity", "my-tip.md"])
        content = (project_dir / ".evolve" / "public" / "guideline" / "my-tip.md").read_text()
        assert "visibility: public" in content

    def test_rejects_path_traversal_in_entity_name(self, project_dir):
        result = run_publish(project_dir, ["--entity", "../../etc/passwd"], expect_success=False)
        assert result.returncode != 0
        assert "invalid entity name" in result.stderr
