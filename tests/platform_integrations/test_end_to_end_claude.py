"""End-to-end data-flow test for the rendered Claude evolve-lite scripts.

This is the ONE integration test that proves the correlation ids line up across
the whole chain on Claude — the integration that was broken in the pre-redesign
world (native transcript path vs. entity id) and the reason the hookless redesign
exists. It drives the REAL rendered Claude scripts as subprocesses, in sequence,
with nothing mocked in the data flow:

    adapt_memory.py  -> mirrors a native memory into the evolve store, emitting
                        the entity id ``feedback/prefer-ripgrep``.
    audit_recall.py  -> records a ``recall`` row keyed by that exact entity id
                        and the host session id.
    provenance.py    -> reads the recall row, resolves the mirrored entity AND
                        the NATIVE Claude transcript, and emits exactly one
                        candidate whose ids line up end to end.
    provenance.py    -> records a ``followed`` verdict, then dedups the pair.

Lib resolution (``lib/evolve-lite/entity_io.py``) only works in the rendered
tree, so we point at the rendered Claude copies under ``platform-integrations/``.

The scripts are driven as real subprocesses (closest to actual agent usage);
nothing in the data flow is mocked.
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
_PLUGIN = _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite"
ADAPT_SCRIPT = _PLUGIN / "skills/evolve-lite/adapt-memory/scripts/adapt_memory.py"
AUDIT_SCRIPT = _PLUGIN / "lib/evolve-lite/audit_recall.py"
PROVENANCE_SCRIPT = _PLUGIN / "skills/evolve-lite/provenance/scripts/provenance.py"

SID = "claude-e2e-session-0001"

NATIVE_MEMORY = """\
---
name: prefer-ripgrep
description: use ripgrep over grep
metadata:
  type: feedback
---
Always reach for ripgrep (rg) instead of grep.
"""


def _claude_slug(root: Path) -> str:
    """Mirror provenance.py / doctor.py slugging: non-alphanumerics -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(root))


def _run(script: Path, *args, evolve_dir: Path, home: Path, cwd: Path, stdin=None, sid=None):
    """Run a rendered Claude script as a real subprocess in the sandbox.

    Every host path is sandboxed: ``$EVOLVE_DIR`` points at the temp store,
    ``$HOME``/``$USERPROFILE`` at a sandboxed home, cwd at the temp project root,
    and ``$CLAUDE_CODE_SESSION_ID`` at a known SID when supplied.
    """
    env = {**os.environ}
    env["EVOLVE_DIR"] = str(evolve_dir)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.pop("HOMEDRIVE", None)
    env.pop("HOMEPATH", None)
    if sid is not None:
        env["CLAUDE_CODE_SESSION_ID"] = sid
    else:
        env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run(
        [sys.executable, str(script), *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        check=False,
    )


def _parse_jsonl(text: str):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _read_audit(evolve_dir: Path):
    path = evolve_dir / "audit.log"
    if not path.is_file():
        return []
    return _parse_jsonl(path.read_text(encoding="utf-8"))


@pytest.fixture
def sandbox(tmp_path, sandbox_home):
    """Build the sandbox dirs the chain needs and return the salient paths.

    ``sandbox_home`` (autouse) already redirects ``$HOME``; we reuse it as the
    home that holds the native Claude transcript tree. The project root lives
    under tmp_path with its own ``.evolve`` store, kept separate from HOME so
    the native-transcript slug (derived from the project root) is exercised for
    real.
    """
    project_root = tmp_path / "proj"
    project_root.mkdir()
    evolve_dir = project_root / ".evolve"
    evolve_dir.mkdir()
    return {
        "home": sandbox_home,
        "project_root": project_root,
        "evolve_dir": evolve_dir,
    }


def test_chain_closes_ids_line_up(sandbox):
    """The whole chain closes: the entity adapt() creates is the entity audit()
    records is the entity provenance() resolves against the native transcript.

    Steps (each runs the real rendered script as a subprocess):
      1. save  — write the native Claude memory file.
      2. adapt — mirror it; assert entities/feedback/prefer-ripgrep.md exists and
                 the printed entity id is ``feedback/prefer-ripgrep``.
      3. audit — record a recall row for that exact entity id under the SID.
      4. native transcript — drop ~/.claude/projects/<slug>/<SID>.jsonl.
      5. candidates — assert EXACTLY ONE candidate whose entity_id ==
                 ``feedback/prefer-ripgrep``, whose excerpt holds the mirrored
                 content, whose trajectory_path is the native transcript, with
                 NO ``missing`` field (entity + trajectory both resolved). This
                 is the id-alignment assertion.
      6. record + dedup — pipe a ``followed`` verdict; assert an influence row is
                 appended; re-run candidates and assert it's now empty.
    """
    home = sandbox["home"]
    project_root = sandbox["project_root"]
    evolve_dir = sandbox["evolve_dir"]

    # --- 1. save: native memory file (Claude format) ------------------------
    native_file = project_root / "native_memory.md"
    native_file.write_text(NATIVE_MEMORY, encoding="utf-8")

    # --- 2. adapt: mirror native memory into the evolve store ---------------
    adapt = _run(
        ADAPT_SCRIPT,
        str(native_file),
        "--type",
        "feedback",
        "--trigger",
        "when searching code, prefer ripgrep",
        evolve_dir=evolve_dir,
        home=home,
        cwd=project_root,
    )
    assert adapt.returncode == 0, adapt.stderr

    mirrored = evolve_dir / "entities" / "feedback" / "prefer-ripgrep.md"
    assert mirrored.is_file(), f"adapt did not mirror the entity: {adapt.stdout}\n{adapt.stderr}"

    # Capture the entity id from adapt's stdout ("Entity id: <id>").
    id_lines = [ln for ln in adapt.stdout.splitlines() if ln.startswith("Entity id:")]
    assert id_lines, f"adapt did not print an entity id:\n{adapt.stdout}"
    adapted_entity_id = id_lines[0].split("Entity id:", 1)[1].strip()
    assert adapted_entity_id == "feedback/prefer-ripgrep"

    # --- 3. audit: record a recall row for that exact entity id -------------
    audit = _run(
        AUDIT_SCRIPT,
        adapted_entity_id,  # exactly as EVOLVE.md instructs the agent to pass it
        evolve_dir=evolve_dir,
        home=home,
        cwd=project_root,
        sid=SID,
    )
    assert audit.returncode == 0, audit.stderr

    recall_rows = [r for r in _read_audit(evolve_dir) if r.get("event") == "recall"]
    assert len(recall_rows) == 1, _read_audit(evolve_dir)
    assert recall_rows[0]["session_id"] == SID
    assert recall_rows[0]["entities"] == ["feedback/prefer-ripgrep"]

    # --- 4. native transcript fixture ---------------------------------------
    slug = _claude_slug(project_root)
    native_transcript = home / ".claude" / "projects" / slug / f"{SID}.jsonl"
    native_transcript.parent.mkdir(parents=True)
    native_transcript.write_text(
        '{"type":"user","message":{"role":"user","content":"search the repo for TODOs"}}\n'
        '{"type":"assistant","message":{"role":"assistant","content":"Using rg to search."}}\n',
        encoding="utf-8",
    )

    # --- 5. candidates: the id-alignment assertion --------------------------
    cand_result = _run(
        PROVENANCE_SCRIPT,
        "candidates",
        evolve_dir=evolve_dir,
        home=home,
        cwd=project_root,
    )
    assert cand_result.returncode == 0, cand_result.stderr
    candidates = _parse_jsonl(cand_result.stdout)
    assert len(candidates) == 1, f"expected exactly one candidate, got: {candidates}"
    cand = candidates[0]

    # KEY ASSERTION: the entity adapt() created == the entity audit() recorded
    # == the entity provenance() resolved, and the native transcript located by
    # the resolved project-root slug lines up with the audited session id.
    assert cand["session_id"] == SID
    assert cand["entity_id"] == adapted_entity_id == "feedback/prefer-ripgrep"
    assert "Always reach for ripgrep (rg) instead of grep." in cand["entity_excerpt"]
    assert cand["trajectory_path"] == str(native_transcript)
    assert "rg to search" in cand["trajectory_excerpt"]
    assert "missing" not in cand, f"chain did not fully resolve: {cand}"

    # --- 6. record a verdict, then assert dedup -----------------------------
    verdict = {
        "session_id": SID,
        "entity": adapted_entity_id,
        "verdict": "followed",
        "evidence": "Assistant used rg (ripgrep) to search the repo.",
    }
    record = _run(
        PROVENANCE_SCRIPT,
        "record",
        evolve_dir=evolve_dir,
        home=home,
        cwd=project_root,
        stdin=json.dumps(verdict),
    )
    assert record.returncode == 0, record.stderr

    influence_rows = [r for r in _read_audit(evolve_dir) if r.get("event") == "influence"]
    assert len(influence_rows) == 1, _read_audit(evolve_dir)
    assert influence_rows[0]["session_id"] == SID
    assert influence_rows[0]["entity"] == "feedback/prefer-ripgrep"
    assert influence_rows[0]["verdict"] == "followed"

    # Re-run candidates: the judged pair is deduped -> nothing left.
    cand_again = _run(
        PROVENANCE_SCRIPT,
        "candidates",
        evolve_dir=evolve_dir,
        home=home,
        cwd=project_root,
    )
    assert cand_again.returncode == 0, cand_again.stderr
    assert _parse_jsonl(cand_again.stdout) == [], cand_again.stdout


def test_candidates_surface_gaps_when_nothing_lines_up(sandbox):
    """Negative/robustness: when the audited entity id was NEVER mirrored AND no
    transcript exists, the candidate is still emitted with ``missing`` listing
    BOTH ``entity`` and ``trajectory`` — the chain surfaces gaps instead of
    silently dropping them.
    """
    home = sandbox["home"]
    project_root = sandbox["project_root"]
    evolve_dir = sandbox["evolve_dir"]

    # Record a recall for an entity id that was never adapted/mirrored, with no
    # native transcript on disk for the session.
    audit = _run(
        AUDIT_SCRIPT,
        "feedback/does-not-exist",
        evolve_dir=evolve_dir,
        home=home,
        cwd=project_root,
        sid="ghost-session-0002",
    )
    assert audit.returncode == 0, audit.stderr

    cand_result = _run(
        PROVENANCE_SCRIPT,
        "candidates",
        evolve_dir=evolve_dir,
        home=home,
        cwd=project_root,
    )
    assert cand_result.returncode == 0, cand_result.stderr
    candidates = _parse_jsonl(cand_result.stdout)
    assert len(candidates) == 1, candidates
    cand = candidates[0]
    assert cand["entity_id"] == "feedback/does-not-exist"
    assert cand["entity_excerpt"] is None
    assert cand["trajectory_path"] is None
    assert set(cand["missing"]) == {"entity", "trajectory"}
