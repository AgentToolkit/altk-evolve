"""Tests for skills/learn/scripts/save_entities.py."""

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
SAVE_SCRIPT = _PLUGIN_ROOT / "skills/learn/scripts/save_entities.py"


def run_save(project_dir, entities, args=None, evolve_dir=None, expect_success=True):
    env = {**os.environ}
    if evolve_dir:
        env["EVOLVE_DIR"] = str(evolve_dir)
    return subprocess.run(
        [sys.executable, str(SAVE_SCRIPT)] + (args or []),
        input=json.dumps({"entities": entities}),
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=expect_success,
    )


class TestSaveEntities:
    def test_writes_entity_file(self, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        run_save(tmp_path, [{"type": "guideline", "content": "Use semantic versioning."}], evolve_dir=evolve_dir)
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert len(files) == 1
        assert "Use semantic versioning." in files[0].read_text()

    def test_sets_visibility_private_by_default(self, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        run_save(tmp_path, [{"type": "guideline", "content": "Commit often."}], evolve_dir=evolve_dir)
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert "visibility: private" in files[0].read_text()

    def test_user_flag_stamps_owner(self, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        run_save(
            tmp_path,
            [{"type": "guideline", "content": "Write clear commit messages."}],
            args=["--user", "alice"],
            evolve_dir=evolve_dir,
        )
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert "owner: alice" in files[0].read_text()

    def test_deduplicates_exact_content(self, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        entity = {"type": "guideline", "content": "No magic numbers."}
        run_save(tmp_path, [entity], evolve_dir=evolve_dir)
        run_save(tmp_path, [entity], evolve_dir=evolve_dir)
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert len(files) == 1

    def test_dedup_is_case_and_whitespace_insensitive(self, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        run_save(tmp_path, [{"type": "guideline", "content": "No magic numbers."}], evolve_dir=evolve_dir)
        run_save(tmp_path, [{"type": "guideline", "content": "NO MAGIC  NUMBERS."}], evolve_dir=evolve_dir)
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert len(files) == 1

    def test_multiple_entities_all_written(self, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        run_save(tmp_path, [
            {"type": "guideline", "content": "First tip."},
            {"type": "guideline", "content": "Second tip."},
        ], evolve_dir=evolve_dir)
        files = list((evolve_dir / "entities" / "guideline").glob("*.md"))
        assert len(files) == 2

    def test_skips_entities_without_content(self, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        run_save(tmp_path, [{"type": "guideline"}], evolve_dir=evolve_dir)
        guideline_dir = evolve_dir / "entities" / "guideline"
        if guideline_dir.exists():
            assert list(guideline_dir.glob("*.md")) == []

    def test_exits_cleanly_when_empty_entities_list(self, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        result = run_save(tmp_path, [], evolve_dir=evolve_dir, expect_success=False)
        assert result.returncode == 0

    def test_output_reports_added_count(self, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        result = run_save(tmp_path, [
            {"type": "guideline", "content": "Tip A."},
            {"type": "guideline", "content": "Tip B."},
        ], evolve_dir=evolve_dir)
        assert "Added 2" in result.stdout
