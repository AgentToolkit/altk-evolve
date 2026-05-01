"""Tests for the Claude plugin's skills/learn/scripts/log_influence.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.platform_integrations, pytest.mark.e2e]

_PLUGIN_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite"
LOG_INFLUENCE_SCRIPT = _PLUGIN_ROOT / "skills/learn/scripts/log_influence.py"


def run_log_influence(project_dir, payload, *, raw_input=None, evolve_dir=None):
    """Invoke log_influence.py with the given payload (dict) or raw_input (str)."""
    env = {**os.environ}
    if evolve_dir:
        env["EVOLVE_DIR"] = str(evolve_dir)
    stdin = raw_input if raw_input is not None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(LOG_INFLUENCE_SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=False,
    )


def read_audit(evolve_dir):
    path = evolve_dir / "audit.log"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestLogInfluence:
    def test_writes_single_assessment(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "abc-123",
                "assessments": [
                    {"entity": "slug-a", "verdict": "followed", "evidence": "because"},
                ],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 0, result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0] == {
            "event": "influence",
            "session_id": "abc-123",
            "entity": "slug-a",
            "verdict": "followed",
            "evidence": "because",
            "ts": events[0]["ts"],
        }

    def test_writes_multiple_assessments(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "sess-1",
                "assessments": [
                    {"entity": "slug-a", "verdict": "followed", "evidence": "e1"},
                    {"entity": "slug-b", "verdict": "not_applicable", "evidence": "e2"},
                    {"entity": "slug-c", "verdict": "contradicted", "evidence": "e3"},
                ],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 0, result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 3
        verdicts = {e["entity"]: e["verdict"] for e in events}
        assert verdicts == {"slug-a": "followed", "slug-b": "not_applicable", "slug-c": "contradicted"}

    def test_skips_assessments_with_invalid_verdict(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "sess-1",
                "assessments": [
                    {"entity": "slug-a", "verdict": "bogus", "evidence": "no"},
                    {"entity": "slug-b", "verdict": "followed", "evidence": "yes"},
                ],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 0, result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0]["entity"] == "slug-b"

    def test_skips_assessments_missing_entity(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "sess-1",
                "assessments": [
                    {"verdict": "followed", "evidence": "no entity"},
                    {"entity": "slug-b", "verdict": "followed", "evidence": "ok"},
                ],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 0, result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0]["entity"] == "slug-b"

    def test_skips_non_dict_assessment_items(self, temp_project_dir):
        """Non-dict items in the assessments list must not raise AttributeError."""
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "sess-1",
                "assessments": [
                    "not-a-dict",
                    42,
                    None,
                    {"entity": "slug-ok", "verdict": "followed", "evidence": "yes"},
                ],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 0, result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0]["entity"] == "slug-ok"

    def test_empty_assessments_list_is_ok(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {"session_id": "sess-1", "assessments": []},
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 0, result.stderr
        assert read_audit(evolve_dir) == []

    def test_evidence_defaults_to_empty_string(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "sess-1",
                "assessments": [{"entity": "slug-a", "verdict": "followed"}],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 0, result.stderr
        events = read_audit(evolve_dir)
        assert events[0]["evidence"] == ""

    def test_rejects_non_dict_payload(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(temp_project_dir, ["not", "a", "dict"], evolve_dir=evolve_dir)
        assert result.returncode == 1
        assert "payload" in result.stderr.lower()
        assert read_audit(evolve_dir) == []

    def test_rejects_missing_session_id(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {"assessments": [{"entity": "a", "verdict": "followed"}]},
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 1
        assert read_audit(evolve_dir) == []

    def test_rejects_non_list_assessments(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {"session_id": "sess-1", "assessments": "oops"},
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 1
        assert read_audit(evolve_dir) == []

    def test_rejects_invalid_json(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(temp_project_dir, None, raw_input="{not valid json", evolve_dir=evolve_dir)
        assert result.returncode == 1
        assert "json" in result.stderr.lower()
        assert read_audit(evolve_dir) == []
