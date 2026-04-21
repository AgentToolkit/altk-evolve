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

pytestmark = pytest.mark.platform_integrations


class TestAuditAppend:
    def test_creates_log_file_on_first_call(self, temp_project_dir):
        audit.append(project_root=str(temp_project_dir), action="test")
        assert (temp_project_dir / ".evolve" / "audit.log").exists()

    def test_creates_parent_directories(self, temp_project_dir):
        nested = temp_project_dir / "deep" / "project"
        nested.mkdir(parents=True)
        audit.append(project_root=str(nested), action="test")
        assert (nested / ".evolve" / "audit.log").exists()

    def test_entry_is_valid_json(self, temp_project_dir):
        audit.append(project_root=str(temp_project_dir), action="publish", actor="alice", entity="tip.md")
        line = (temp_project_dir / ".evolve" / "audit.log").read_text().strip()
        entry = json.loads(line)
        assert entry["action"] == "publish"
        assert entry["actor"] == "alice"
        assert entry["entity"] == "tip.md"

    def test_timestamp_field_present_and_utc(self, temp_project_dir):
        audit.append(project_root=str(temp_project_dir), action="sync")
        entry = json.loads((temp_project_dir / ".evolve" / "audit.log").read_text().strip())
        assert "ts" in entry
        assert entry["ts"].endswith("Z")

    def test_multiple_calls_produce_multiple_lines(self, temp_project_dir):
        audit.append(project_root=str(temp_project_dir), action="subscribe", name="alice")
        audit.append(project_root=str(temp_project_dir), action="sync")
        audit.append(project_root=str(temp_project_dir), action="unsubscribe", name="alice")
        lines = (temp_project_dir / ".evolve" / "audit.log").read_text().splitlines()
        assert len(lines) == 3
        actions = [json.loads(line)["action"] for line in lines]
        assert actions == ["subscribe", "sync", "unsubscribe"]

    def test_extra_fields_are_preserved(self, temp_project_dir):
        audit.append(project_root=str(temp_project_dir), action="sync", delta={"alice": {"added": 2}})
        entry = json.loads((temp_project_dir / ".evolve" / "audit.log").read_text().strip())
        assert entry["delta"]["alice"]["added"] == 2
