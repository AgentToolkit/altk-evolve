"""Tests for evolve-lite lib/audit.py — append-only JSON audit log."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite/lib"),
)
import audit

pytestmark = [pytest.mark.platform_integrations, pytest.mark.unit]


class TestAuditAppend:
    def test_creates_log_file_on_first_call(self, tmp_path):
        audit.append(project_root=str(tmp_path), action="test")
        assert (tmp_path / ".evolve" / "audit.log").exists()

    def test_creates_parent_directories(self, tmp_path):
        nested = tmp_path / "deep" / "project"
        nested.mkdir(parents=True)
        audit.append(project_root=str(nested), action="test")
        assert (nested / ".evolve" / "audit.log").exists()

    def test_entry_is_valid_json(self, temp_project_dir):
        audit.append(project_root=str(temp_project_dir), action="publish", actor="alice", entity="guideline.md")
        line = (temp_project_dir / ".evolve" / "audit.log").read_text().strip()
        entry = json.loads(line)
        assert entry["action"] == "publish"
        assert entry["actor"] == "alice"
        assert entry["entity"] == "guideline.md"

    def test_timestamp_field_present_and_utc(self, tmp_path):
        audit.append(project_root=str(tmp_path), action="sync")
        entry = json.loads((tmp_path / ".evolve" / "audit.log").read_text().strip())
        assert "ts" in entry
        assert entry["ts"].endswith("Z")

    def test_multiple_calls_produce_multiple_lines(self, tmp_path):
        audit.append(project_root=str(tmp_path), action="subscribe", name="alice")
        audit.append(project_root=str(tmp_path), action="sync")
        audit.append(project_root=str(tmp_path), action="unsubscribe", name="alice")
        lines = (tmp_path / ".evolve" / "audit.log").read_text().splitlines()
        assert len(lines) == 3
        actions = [json.loads(line)["action"] for line in lines]
        assert actions == ["subscribe", "sync", "unsubscribe"]

    def test_extra_fields_are_preserved(self, tmp_path):
        audit.append(project_root=str(tmp_path), action="sync", delta={"alice": {"added": 2}})
        entry = json.loads((tmp_path / ".evolve" / "audit.log").read_text().strip())
        assert entry["delta"]["alice"]["added"] == 2
