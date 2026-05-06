#!/usr/bin/env python3
"""Append post-hoc influence assessments to .evolve/audit.log.

Reads JSON from stdin of the form:
  {
    "session_id": "<transcript stem>",
    "assessments": [
      {"entity": "<qualified id>", "verdict": "followed|contradicted|not_applicable",
       "evidence": "<short justification>"},
      ...
    ]
  }
"""

import json
import sys
from pathlib import Path

# Walk up from the script location to find the installed plugin lib directory.
# claude/claw-code/codex/bob all ship a sibling lib/ next to skills/; bob's
# installer copies it to .bob/evolve-lib/, hence both names are checked.
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    for _candidate in (_ancestor / "lib", _ancestor / "evolve-lib"):
        if (_candidate / "entity_io.py").is_file():
            _lib = _candidate
            break
    if _lib is not None:
        break
if _lib is None:
    raise ImportError(f"Cannot find plugin lib directory above {_script}")
sys.path.insert(0, str(_lib))
from entity_io import get_evolve_dir, log as _log  # noqa: E402
import audit  # noqa: E402


_ALLOWED_VERDICTS = {"followed", "contradicted", "not_applicable"}


def log(message):
    _log("influence", message)


def existing_influence_keys(evolve_dir):
    audit_log = Path(evolve_dir) / "audit.log"
    if not audit_log.is_file():
        return set()

    keys = set()
    for line in audit_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "influence" and event.get("session_id") and event.get("entity"):
            keys.add((event["session_id"], event["entity"]))
    return keys


def main():
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        log(f"Invalid JSON input: {exc}")
        print(f"Error: invalid JSON input - {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(payload, dict):
        log(f"Bad payload type: {type(payload).__name__}")
        print("Error: payload must be a JSON object.", file=sys.stderr)
        sys.exit(1)

    session_id = payload.get("session_id")
    assessments = payload.get("assessments", [])
    if not session_id or not isinstance(assessments, list):
        log(f"Bad payload shape: session_id={session_id!r} assessments_type={type(assessments).__name__}")
        print("Error: payload must include `session_id` and a list `assessments`.", file=sys.stderr)
        sys.exit(1)

    evolve_dir = get_evolve_dir().resolve()
    existing_keys = existing_influence_keys(evolve_dir)

    written = 0
    for assessment in assessments:
        if not isinstance(assessment, dict):
            log(f"Skipping non-dict assessment item: {assessment!r}")
            continue
        entity = assessment.get("entity")
        verdict = assessment.get("verdict")
        evidence = assessment.get("evidence", "")
        if not entity or verdict not in _ALLOWED_VERDICTS:
            log(f"Skipping invalid assessment: {assessment}")
            continue
        key = (session_id, entity)
        if key in existing_keys:
            log(f"Skipping duplicate influence assessment: session_id={session_id} entity={entity}")
            continue
        audit.append(
            evolve_dir=str(evolve_dir),
            event="influence",
            session_id=session_id,
            entity=entity,
            verdict=verdict,
            evidence=evidence,
        )
        existing_keys.add(key)
        written += 1

    log(f"Wrote {written} influence record(s) for session {session_id}")
    print(f"Recorded {written} influence assessment(s).")


if __name__ == "__main__":
    main()
