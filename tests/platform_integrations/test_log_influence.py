"""Tests for skills/evolve-lite/provenance/scripts/log_influence.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.platform_integrations, pytest.mark.e2e]

_REPO_ROOT = Path(__file__).parent.parent.parent
LOG_INFLUENCE_SCRIPT = (
    _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite/skills/evolve-lite/provenance/scripts/log_influence.py"
)
# Every rendered copy of the writer. Each resolves its own lib/evolve-lite/, so a
# platform-specific render or lib problem shows up here rather than in a session.
RENDERED_LOG_INFLUENCE_SCRIPTS = {
    "claude": LOG_INFLUENCE_SCRIPT,
    "codex": _REPO_ROOT / "platform-integrations/codex/plugins/evolve-lite/skills/evolve-lite/provenance/scripts/log_influence.py",
    "claw-code": _REPO_ROOT / "platform-integrations/claw-code/plugins/evolve-lite/skills/evolve-lite/provenance/scripts/log_influence.py",
    "bob": _REPO_ROOT / "platform-integrations/bob/evolve-lite/skills/evolve-lite-provenance/scripts/log_influence.py",
}

ALL_VERDICTS = ("followed", "contradicted", "not_applicable", "insufficient_evidence")


def run_log_influence(project_dir, payload, *, raw_input=None, evolve_dir=None, script=LOG_INFLUENCE_SCRIPT):
    """Invoke log_influence.py with the given payload dict or raw input string."""
    env = {**os.environ}
    if evolve_dir:
        env["EVOLVE_DIR"] = str(evolve_dir)
    stdin = raw_input if raw_input is not None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(script)],
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
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestLogInfluence:
    def test_writes_single_assessment(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "abc-123",
                "assessments": [
                    {"entity": "guideline/slug-a", "verdict": "followed", "evidence": "because"},
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
            "entity": "guideline/slug-a",
            "verdict": "followed",
            "evidence": "because",
            "ts": events[0]["ts"],
        }

    def test_writes_under_custom_evolve_dir(self, temp_project_dir):
        evolve_dir = temp_project_dir / "custom-evolve-data"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "abc-123",
                "assessments": [
                    {"entity": "guideline/slug-a", "verdict": "followed", "evidence": "because"},
                ],
            },
            evolve_dir=evolve_dir,
        )

        assert result.returncode == 0, result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0]["event"] == "influence"
        assert not (temp_project_dir / ".evolve" / "audit.log").exists()

    def test_writes_multiple_assessments(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "sess-1",
                "assessments": [
                    {"entity": "guideline/slug-a", "verdict": "followed", "evidence": "e1"},
                    {"entity": "guideline/slug-b", "verdict": "not_applicable", "evidence": "e2"},
                    {"entity": "guideline/slug-c", "verdict": "contradicted", "evidence": "e3"},
                ],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 0, result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 3
        verdicts = {event["entity"]: event["verdict"] for event in events}
        assert verdicts == {
            "guideline/slug-a": "followed",
            "guideline/slug-b": "not_applicable",
            "guideline/slug-c": "contradicted",
        }

    def test_skips_duplicate_assessments_on_rerun(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        payload = {
            "session_id": "sess-1",
            "assessments": [
                {"entity": "guideline/slug-a", "verdict": "followed", "evidence": "e1"},
                {"entity": "guideline/slug-a", "verdict": "contradicted", "evidence": "e2"},
            ],
        }

        first = run_log_influence(temp_project_dir, payload, evolve_dir=evolve_dir)
        second = run_log_influence(temp_project_dir, payload, evolve_dir=evolve_dir)

        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0]["entity"] == "guideline/slug-a"
        assert events[0]["verdict"] == "followed"
        # A duplicate is expected on a rerun, so it is not an error, but the
        # summary says it happened instead of looking like a clean write.
        assert "Recorded 0 influence assessment(s)." in second.stdout
        assert "Skipped 2 already recorded." in second.stdout

    @pytest.mark.parametrize("platform", sorted(RENDERED_LOG_INFLUENCE_SCRIPTS))
    def test_records_insufficient_evidence(self, temp_project_dir, platform):
        """An abstention is a verdict to record, not an invalid one to drop (#340)."""
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "s1",
                "assessments": [
                    {"entity": "guideline/a", "verdict": "followed", "evidence": "e"},
                    {"entity": "guideline/b", "verdict": "insufficient_evidence", "evidence": "not enough"},
                ],
            },
            evolve_dir=evolve_dir,
            script=RENDERED_LOG_INFLUENCE_SCRIPTS[platform],
        )
        assert result.returncode == 0, result.stderr
        assert "Recorded 2 influence assessment(s)." in result.stdout
        verdicts = {event["entity"]: event["verdict"] for event in read_audit(evolve_dir)}
        assert verdicts == {"guideline/a": "followed", "guideline/b": "insufficient_evidence"}

    def test_accepts_every_documented_verdict(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "s1",
                "assessments": [{"entity": f"guideline/{v}", "verdict": v, "evidence": "e"} for v in ALL_VERDICTS],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 0, result.stderr
        assert [event["verdict"] for event in read_audit(evolve_dir)] == list(ALL_VERDICTS)

    def test_rejects_invalid_verdict_loudly_and_keeps_valid_ones(self, temp_project_dir):
        """An unknown verdict exits 1 and is named, so the judgment is never lost behind exit 0."""
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "sess-1",
                "assessments": [
                    {"entity": "guideline/slug-a", "verdict": "bogus", "evidence": "no"},
                    {"entity": "guideline/slug-b", "verdict": "followed", "evidence": "yes"},
                ],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 1
        assert "Recorded 1 influence assessment(s)." in result.stdout
        assert "rejected 1 assessment(s)" in result.stderr
        assert "assessments[0] (guideline/slug-a)" in result.stderr
        assert "'bogus'" in result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0]["entity"] == "guideline/slug-b"

    @pytest.mark.parametrize("verdict", [["followed"], {"v": "followed"}, None, 1])
    def test_rejects_a_non_string_verdict_without_crashing(self, temp_project_dir, verdict):
        """An unhashable verdict used to raise TypeError mid-batch and drop the items after it."""
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "sess-1",
                "assessments": [
                    {"entity": "guideline/slug-a", "verdict": verdict, "evidence": "no"},
                    {"entity": "guideline/slug-b", "verdict": "followed", "evidence": "yes"},
                ],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 1
        assert "Traceback" not in result.stderr
        assert "assessments[0] (guideline/slug-a)" in result.stderr
        assert [event["entity"] for event in read_audit(evolve_dir)] == ["guideline/slug-b"]

    def test_resending_a_fixed_batch_records_only_the_fix(self, temp_project_dir):
        """The recovery the error message asks for: fix the verdict, resend everything."""
        evolve_dir = temp_project_dir / ".evolve"
        batch = [
            {"entity": "guideline/slug-a", "verdict": "Followed", "evidence": "e1"},
            {"entity": "guideline/slug-b", "verdict": "followed", "evidence": "e2"},
        ]
        first = run_log_influence(temp_project_dir, {"session_id": "sess-1", "assessments": batch}, evolve_dir=evolve_dir)
        assert first.returncode == 1

        batch[0]["verdict"] = "followed"
        second = run_log_influence(temp_project_dir, {"session_id": "sess-1", "assessments": batch}, evolve_dir=evolve_dir)
        assert second.returncode == 0, second.stderr
        assert "Recorded 1 influence assessment(s). Skipped 1 already recorded." in second.stdout
        assert sorted(event["entity"] for event in read_audit(evolve_dir)) == ["guideline/slug-a", "guideline/slug-b"]

    def test_rejects_assessments_missing_entity(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "sess-1",
                "assessments": [
                    {"verdict": "followed", "evidence": "no entity"},
                    {"entity": "guideline/slug-b", "verdict": "followed", "evidence": "ok"},
                ],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 1
        assert "assessments[0]: must include a non-empty string `entity`" in result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0]["entity"] == "guideline/slug-b"

    def test_rejects_non_dict_assessment_items(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        result = run_log_influence(
            temp_project_dir,
            {
                "session_id": "sess-1",
                "assessments": [
                    "not-a-dict",
                    42,
                    None,
                    {"entity": "guideline/slug-ok", "verdict": "followed", "evidence": "yes"},
                ],
            },
            evolve_dir=evolve_dir,
        )
        assert result.returncode == 1
        assert "rejected 3 assessment(s)" in result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0]["entity"] == "guideline/slug-ok"

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
                "assessments": [{"entity": "guideline/slug-a", "verdict": "followed"}],
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
            {"assessments": [{"entity": "guideline/a", "verdict": "followed"}]},
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
