"""Tests for skills/evolve-lite/provenance/scripts/provenance.py.

These exercise the rendered Claude provenance.py end to end (lib resolution only
works in the rendered tree). They cover the deterministic plumbing — recall-row
reading, entity resolution, the trajectory locator (BOTH legacy
``.evolve/trajectories/`` and the native ``~/.claude/projects/<slug>/`` paths),
dedup against existing influence rows, and the ``record`` writer. The semantic
verdict is agent-driven and is NOT tested here (there is no heuristic to test).
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.platform_integrations]

_REPO_ROOT = Path(__file__).parent.parent.parent
PROVENANCE_SCRIPT = _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite/skills/evolve-lite/provenance/scripts/provenance.py"


def _claude_slug(root: Path) -> str:
    """Mirror provenance.py / doctor.py slugging: non-alphanumerics -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(root))


def run_provenance(mode, *, evolve_dir, home=None, cwd=None, stdin=None):
    env = {**os.environ}
    env["EVOLVE_DIR"] = str(evolve_dir)
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
    return subprocess.run(
        [sys.executable, str(PROVENANCE_SCRIPT), mode],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=False,
    )


def parse_jsonl(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def read_audit(evolve_dir):
    path = Path(evolve_dir) / "audit.log"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_audit(evolve_dir, rows):
    path = Path(evolve_dir) / "audit.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def write_entity(evolve_dir, entity_id, body="Do the foo thing."):
    path = Path(evolve_dir) / "entities" / f"{entity_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: {entity_id.split('/')[0]}\ntrigger: when foo\n---\n\n{body}\n", encoding="utf-8")
    return path


class TestCandidatesLegacyTrajectory:
    def test_resolves_entity_and_legacy_trajectory(self, tmp_path):
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(evolve_dir, [{"event": "recall", "session_id": "sid-1", "entities": ["feedback/foo"]}])
        write_entity(evolve_dir, "feedback/foo")
        traj = evolve_dir / "trajectories" / "claude-transcript_sid-1.jsonl"
        traj.parent.mkdir(parents=True)
        traj.write_text('{"type":"user","content":"hi"}\n', encoding="utf-8")

        result = run_provenance("candidates", evolve_dir=evolve_dir)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand["session_id"] == "sid-1"
        assert cand["entity_id"] == "feedback/foo"
        assert "Do the foo thing." in cand["entity_excerpt"]
        assert cand["trajectory_path"] == str(traj)
        assert "hi" in cand["trajectory_excerpt"]
        assert "missing" not in cand


class TestCandidatesNativeTranscript:
    def test_locates_native_claude_transcript(self, tmp_path):
        # Sandbox a fake HOME and project root; the native locator builds
        # ~/.claude/projects/<slug>/<sid>.jsonl from the RESOLVED project root.
        home = tmp_path / "home"
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(evolve_dir, [{"event": "recall", "session_id": "nat-1", "entities": ["feedback/bar"]}])
        write_entity(evolve_dir, "feedback/bar", body="bar guidance")

        project_root = evolve_dir.resolve().parent
        slug = _claude_slug(project_root)
        native = home / ".claude" / "projects" / slug / "nat-1.jsonl"
        native.parent.mkdir(parents=True)
        native.write_text('{"x":1}\n', encoding="utf-8")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand["entity_id"] == "feedback/bar"
        assert cand["trajectory_path"] == str(native)
        assert "missing" not in cand


class TestCandidatesMissing:
    def test_missing_trajectory_still_emitted(self, tmp_path):
        # Empty HOME -> no native transcript, no legacy dir -> trajectory missing.
        home = tmp_path / "home"
        home.mkdir()
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(evolve_dir, [{"event": "recall", "session_id": "sid-x", "entities": ["feedback/foo"]}])
        write_entity(evolve_dir, "feedback/foo")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        assert candidates[0]["trajectory_path"] is None
        assert candidates[0]["missing"] == ["trajectory"]

    def test_missing_entity_still_emitted(self, tmp_path):
        home = tmp_path / "home"
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(evolve_dir, [{"event": "recall", "session_id": "sid-y", "entities": ["feedback/ghost"]}])
        traj = evolve_dir / "trajectories" / "claude-transcript_sid-y.jsonl"
        traj.parent.mkdir(parents=True)
        traj.write_text("{}\n", encoding="utf-8")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        assert candidates[0]["entity_excerpt"] is None
        assert candidates[0]["missing"] == ["entity"]


class TestCandidatesDedup:
    def test_skips_pairs_with_existing_influence_row(self, tmp_path):
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(
            evolve_dir,
            [
                {"event": "recall", "session_id": "sid-1", "entities": ["feedback/foo", "feedback/bar"]},
                {"event": "influence", "session_id": "sid-1", "entity": "feedback/foo", "verdict": "followed", "evidence": "x"},
            ],
        )
        write_entity(evolve_dir, "feedback/foo")
        write_entity(evolve_dir, "feedback/bar")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=tmp_path / "home")
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        ids = {c["entity_id"] for c in candidates}
        # feedback/foo already assessed -> only feedback/bar remains.
        assert ids == {"feedback/bar"}


class TestRecord:
    def test_writes_valid_influence_row(self, tmp_path):
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        payload = {
            "session_id": "sid-1",
            "entity": "feedback/foo",
            "verdict": "followed",
            "evidence": "Agent used the saved parser first.",
        }
        result = run_provenance("record", evolve_dir=evolve_dir, stdin=json.dumps(payload))
        assert result.returncode == 0, result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        row = events[0]
        assert row["event"] == "influence"
        assert row["session_id"] == "sid-1"
        assert row["entity"] == "feedback/foo"
        assert row["verdict"] == "followed"
        assert row["evidence"] == "Agent used the saved parser first."
        assert "ts" in row

    def test_rejects_invalid_verdict(self, tmp_path):
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        payload = {"session_id": "sid-1", "entity": "feedback/foo", "verdict": "bogus", "evidence": "no"}
        result = run_provenance("record", evolve_dir=evolve_dir, stdin=json.dumps(payload))
        assert result.returncode == 1
        assert "verdict" in result.stderr.lower()
        assert read_audit(evolve_dir) == []

    def test_record_dedups_existing_pair(self, tmp_path):
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        payload = {"session_id": "sid-1", "entity": "feedback/foo", "verdict": "followed", "evidence": "e"}
        first = run_provenance("record", evolve_dir=evolve_dir, stdin=json.dumps(payload))
        second = run_provenance(
            "record",
            evolve_dir=evolve_dir,
            stdin=json.dumps({**payload, "verdict": "contradicted", "evidence": "e2"}),
        )
        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0]["verdict"] == "followed"
