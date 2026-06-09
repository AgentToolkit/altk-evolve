#!/usr/bin/env python3
"""Deterministic provenance plumbing for the evolve-lite provenance skill.

This script does the *mechanical* half of provenance — the part that can be
made deterministic and tested end to end:

  * read ``recall`` rows from ``.evolve/audit.log``,
  * skip ``(session_id, entity)`` pairs that already have an ``influence`` row,
  * resolve each recalled entity file and the session's trajectory transcript,
  * assemble candidate dicts the agent can judge.

The *semantic* half — deciding whether a recalled guideline was ``followed``,
``contradicted`` or ``not_applicable`` — is an LLM judgment and is NOT done
here. There is deliberately no heuristic verdict: this module never invents a
verdict. The agent reads each candidate, judges it, and pipes the verdict back
through ``record`` (which delegates to ``log_influence.py``'s writer so the
audit-log format is identical).

Two modes:

  candidates  (default)  — emit one JSONL candidate per unresolved
                           (session_id, entity) recall pair to stdout.
  record                 — read a verdict JSON from stdin and append an
                           ``influence`` row via log_influence.py's writer.
"""

import json
import sys
from pathlib import Path

# Walk up from the script location to find the installed plugin lib directory.
# Every host installs the shared lib under lib/evolve-lite/ so multiple
# plugins can coexist side by side (e.g. .bob/lib/evolve-lite/).
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    _candidate = _ancestor / "lib" / "evolve-lite"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
        break
if _lib is None:
    raise ImportError(f"Cannot find plugin lib directory above {_script}")
sys.path.insert(0, str(_lib))

# Provenance reuses log_influence.py's writer + dedup so the audit-log format and
# the duplicate-suppression rule live in exactly one place. log_influence.py sits
# next to this file in the same skill scripts/ directory.
sys.path.insert(0, str(_script.parent))

from entity_io import get_evolve_dir, log as _log  # noqa: E402
import log_influence  # noqa: E402

_ALLOWED_VERDICTS = log_influence._ALLOWED_VERDICTS

# How many characters of the entity file / trajectory to surface in a candidate.
_ENTITY_EXCERPT_CHARS = 4000
_TRAJECTORY_EXCERPT_CHARS = 4000


def log(message):
    _log("provenance", message)


# ---------------------------------------------------------------------------
# Trajectory locator (Task B)
# ---------------------------------------------------------------------------


def _claude_transcript_slug(root):
    """Claude derives a project's transcript dir name by replacing every
    non-alphanumeric character in the absolute project path with ``-``.

    e.g. /Users/x/Documents/kaizen -> -Users-x-Documents-kaizen

    This mirrors ``_transcript_slug`` in the doctor skill
    (skills/evolve-lite/doctor/scripts/doctor.py). The two are kept in sync by
    hand because doctor and provenance ship as independent scripts that do not
    import one another in the rendered tree; if you change one, change both.
    """
    import re

    return re.sub(r"[^A-Za-z0-9]", "-", str(root))


def locate_trajectory(session_id, evolve_dir, *, project_root=None, home=None):
    """Locate the saved trajectory transcript for ``session_id``.

    Resolution order (best-effort, returns the first hit or ``None``):

    1. Legacy ``.evolve/trajectories/`` files:
       * ``claude-transcript_<sid>.jsonl`` — stop-hook transcript dump.
       * ``trajectory_<ts>_<sid>.json`` — save-trajectory skill output; the sid
         is the filename slice after the timestamp.
       * ``trajectory_<ts>.json`` — open and match the inner ``session_id``.
    2. NEW native Claude transcript: ``~/.claude/projects/<slug>/<sid>.jsonl``
       where ``<slug>`` is the project root path slugified the way Claude does
       (every non-alphanumeric char -> ``-``; see ``_claude_transcript_slug``).

    Native discovery makes provenance work in the hookless world where no
    ``.evolve/trajectories/`` file is ever written. It is platform-neutral:
    Bob/Codex keep their transcripts elsewhere, so the native step simply falls
    through to ``None`` for them rather than misfiring.
    """
    evolve_dir = Path(evolve_dir)

    # --- 1. Legacy .evolve/trajectories/ ------------------------------------
    traj_dir = evolve_dir / "trajectories"
    if traj_dir.is_dir():
        direct = traj_dir / f"claude-transcript_{session_id}.jsonl"
        if direct.is_file():
            return direct

        # trajectory_<ts>_<sid>.json — match on the filename sid slice.
        for path in sorted(traj_dir.glob("trajectory_*_*.json")):
            stem = path.stem  # trajectory_<ts>_<sid>
            parts = stem.split("_", 2)
            if len(parts) == 3 and parts[2] == session_id:
                return path

        # trajectory_<ts>.json — open and match the inner session_id field.
        for path in sorted(traj_dir.glob("trajectory_*.json")):
            # Skip the <ts>_<sid> shape already handled above.
            if path.stem.count("_") >= 2:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("session_id") == session_id:
                return path

    # --- 2. Native Claude transcript ----------------------------------------
    # The project root is the parent of the .evolve dir; the home dir holds
    # ~/.claude/projects/<slug>/<sid>.jsonl.
    root = Path(project_root) if project_root is not None else evolve_dir.resolve().parent
    base = Path(home) if home is not None else Path.home()
    slug = _claude_transcript_slug(root)
    native = base / ".claude" / "projects" / slug / f"{session_id}.jsonl"
    if native.is_file():
        return native

    return None


# ---------------------------------------------------------------------------
# Recall row reading + entity resolution (Task A — candidates)
# ---------------------------------------------------------------------------


def read_recall_rows(evolve_dir):
    """Return a list of ``(session_id, [entity_id, ...])`` tuples for every ``recall`` audit row.

    Rows with no ``session_id`` or an empty ``entities`` list are skipped.
    """
    audit_log = Path(evolve_dir) / "audit.log"
    if not audit_log.is_file():
        return []

    rows = []
    for line in audit_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("event") != "recall":
            continue
        session_id = event.get("session_id")
        entities = event.get("entities")
        if not isinstance(session_id, str) or not session_id:
            continue
        if not isinstance(entities, list) or not entities:
            continue
        clean = [e for e in entities if isinstance(e, str) and e]
        if clean:
            rows.append((session_id, clean))
    return rows


def _read_entity(evolve_dir, entity_id):
    """Return ``(path, excerpt)`` for an entity file, or ``(path, None)`` if
    the file is missing. ``entity_id`` is a ``<type>/<name>`` id relative to
    ``entities/`` (without ``.md``).
    """
    entity_path = Path(evolve_dir) / "entities" / f"{entity_id}.md"
    if not entity_path.is_file():
        return entity_path, None
    try:
        text = entity_path.read_text(encoding="utf-8")
    except OSError:
        return entity_path, None
    return entity_path, text[:_ENTITY_EXCERPT_CHARS]


def _read_trajectory_excerpt(trajectory_path):
    """Return a bounded text excerpt of the trajectory file, or ``None``."""
    if trajectory_path is None:
        return None
    try:
        text = Path(trajectory_path).read_text(encoding="utf-8")
    except OSError:
        return None
    return text[:_TRAJECTORY_EXCERPT_CHARS]


def build_candidates(evolve_dir, *, project_root=None, home=None):
    """Assemble candidate dicts for every unresolved recall (session, entity).

    Returns a list of dicts shaped::

        {
            "session_id": ...,
            "entity_id": "<type>/<name>",
            "entity_excerpt": <str or None>,
            "trajectory_path": <str or None>,
            "trajectory_excerpt": <str or None>,
            "missing": ["entity"|"trajectory", ...],   # only when non-empty
        }

    ``(session_id, entity)`` pairs that already have an ``influence`` row are
    skipped via ``log_influence.existing_influence_keys`` — the same dedup rule
    used when influence rows are written. Candidates whose entity file or
    trajectory cannot be found are still emitted with a ``missing`` list so the
    gap is visible rather than silently dropped.
    """
    evolve_dir = Path(evolve_dir)
    existing = log_influence.existing_influence_keys(evolve_dir)

    candidates = []
    for session_id, entities in read_recall_rows(evolve_dir):
        trajectory_path = locate_trajectory(session_id, evolve_dir, project_root=project_root, home=home)
        for entity_id in entities:
            if (session_id, entity_id) in existing:
                continue
            entity_path, entity_excerpt = _read_entity(evolve_dir, entity_id)
            trajectory_excerpt = _read_trajectory_excerpt(trajectory_path)

            missing = []
            if entity_excerpt is None:
                missing.append("entity")
            if trajectory_path is None:
                missing.append("trajectory")

            candidate = {
                "session_id": session_id,
                "entity_id": entity_id,
                "entity_excerpt": entity_excerpt,
                "trajectory_path": str(trajectory_path) if trajectory_path else None,
                "trajectory_excerpt": trajectory_excerpt,
            }
            if missing:
                candidate["missing"] = missing
            candidates.append(candidate)
    return candidates


# ---------------------------------------------------------------------------
# record (Task A — record)
# ---------------------------------------------------------------------------


def record_verdict(payload, evolve_dir=None):
    """Append a single ``influence`` row from an agent verdict.

    ``payload`` is ``{session_id, entity, verdict, evidence}``. The verdict must
    be one of ``followed|contradicted|not_applicable`` (the *semantic* judgment
    stays agent-driven — this only persists what the agent decided). Writing is
    delegated to ``log_influence.py`` so the audit-log row format and the
    duplicate-suppression rule are not duplicated here.

    Returns the number of rows written (0 or 1). Raises ``ValueError`` on an
    invalid payload / verdict.
    """
    if not isinstance(payload, dict):
        raise ValueError("verdict payload must be a JSON object")

    session_id = payload.get("session_id")
    entity = payload.get("entity")
    verdict = payload.get("verdict")
    evidence = payload.get("evidence", "")

    if not isinstance(session_id, str) or not session_id:
        raise ValueError("verdict payload must include a non-empty string session_id")
    if not isinstance(entity, str) or not entity:
        raise ValueError("verdict payload must include a non-empty string entity")
    if verdict not in _ALLOWED_VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(_ALLOWED_VERDICTS)}, got {verdict!r}")

    if evolve_dir is None:
        evolve_dir = get_evolve_dir().resolve()
    evolve_dir = Path(evolve_dir)

    existing = log_influence.existing_influence_keys(evolve_dir)
    if (session_id, entity) in existing:
        log(f"Skipping duplicate influence verdict: session_id={session_id} entity={entity}")
        return 0

    if not isinstance(evidence, str):
        evidence = str(evidence)

    log_influence.audit.append(
        evolve_dir=str(evolve_dir),
        event="influence",
        session_id=session_id,
        entity=entity,
        verdict=verdict,
        evidence=evidence,
    )
    return 1


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------


def _run_candidates():
    evolve_dir = get_evolve_dir().resolve()
    candidates = build_candidates(evolve_dir)
    for candidate in candidates:
        print(json.dumps(candidate))
    log(f"Emitted {len(candidates)} candidate(s) from {evolve_dir}")


def _run_record():
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        log(f"Invalid JSON input: {exc}")
        print(f"Error: invalid JSON input - {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        written = record_verdict(payload)
    except ValueError as exc:
        log(f"Rejected verdict: {exc}")
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    log(f"Recorded {written} influence verdict(s).")
    print(f"Recorded {written} influence verdict(s).")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else "candidates"
    if mode == "candidates":
        _run_candidates()
    elif mode == "record":
        _run_record()
    else:
        print(f"Error: unknown mode {mode!r}; expected 'candidates' or 'record'.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
