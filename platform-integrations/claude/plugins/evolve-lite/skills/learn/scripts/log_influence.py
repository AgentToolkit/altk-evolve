#!/usr/bin/env python3
"""Append post-hoc influence assessments to .evolve/audit.log.

Reads JSON from stdin of the form:
  {
    "session_id": "<transcript stem>",
    "assessments": [
      {"entity": "<slug>", "verdict": "followed|contradicted|not_applicable",
       "evidence": "<short justification>"},
      ...
    ]
  }
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from entity_io import get_evolve_dir, log as _log  # noqa: E402
import audit  # noqa: E402


_ALLOWED_VERDICTS = {"followed", "contradicted", "not_applicable"}


def log(message):
    _log("influence", message)


def main():
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        log(f"Invalid JSON input: {exc}")
        print(f"Error: invalid JSON input - {exc}", file=sys.stderr)
        sys.exit(1)

    session_id = payload.get("session_id")
    assessments = payload.get("assessments", [])
    if not session_id or not isinstance(assessments, list):
        log(f"Bad payload shape: session_id={session_id!r} assessments_type={type(assessments).__name__}")
        print("Error: payload must include `session_id` and a list `assessments`.", file=sys.stderr)
        sys.exit(1)

    project_root = str(get_evolve_dir().resolve().parent)

    written = 0
    for a in assessments:
        entity = a.get("entity")
        verdict = a.get("verdict")
        evidence = a.get("evidence", "")
        if not entity or verdict not in _ALLOWED_VERDICTS:
            log(f"Skipping invalid assessment: {a}")
            continue
        audit.append(
            project_root=project_root,
            event="influence",
            session_id=session_id,
            entity=entity,
            verdict=verdict,
            evidence=evidence,
        )
        written += 1

    log(f"Wrote {written} influence record(s) for session {session_id}")
    print(f"Recorded {written} influence assessment(s).")


if __name__ == "__main__":
    main()
