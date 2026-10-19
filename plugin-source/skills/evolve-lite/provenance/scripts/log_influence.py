#!/usr/bin/env python3
"""Append post-hoc influence assessments to .evolve/audit.log.

Reads JSON from stdin of the form:
  {
    "session_id": "<transcript stem>",
    "assessments": [
      {"entity": "<qualified id>",
       "verdict": "followed|contradicted|not_applicable|insufficient_evidence",
       "evidence": "<short justification>"},
      ...
    ]
  }

Valid assessments are written even when others in the batch are not. Any
assessment that cannot be recorded (not an object, no entity, a verdict outside
``_ALLOWED_VERDICTS``, or a verdict that conflicts with another assessment of
the same entity in this batch) is named on stderr and makes the script exit 1,
so a judgment is never lost behind a zero exit. Resending the whole batch after
fixing it is safe: pairs already recorded are skipped as duplicates. A recorded
verdict is final, so a resend cannot change it.
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
from entity_io import get_evolve_dir, log as _log  # noqa: E402
import audit  # noqa: E402


# ``insufficient_evidence`` is the judge abstaining: the trajectory does not show
# whether the guideline was followed. It is recorded rather than forced into one
# of the other three so that coverage and accuracy can be measured separately.
_ALLOWED_VERDICTS = {"followed", "contradicted", "not_applicable", "insufficient_evidence"}


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
    if not isinstance(session_id, str) or not session_id or not isinstance(assessments, list):
        log(f"Bad payload shape: session_id={session_id!r} assessments_type={type(assessments).__name__}")
        print("Error: payload must include a string `session_id` and a list `assessments`.", file=sys.stderr)
        sys.exit(1)

    evolve_dir = get_evolve_dir().resolve()
    existing_keys = existing_influence_keys(evolve_dir)

    rejected = []
    valid = []
    for index, assessment in enumerate(assessments):
        if not isinstance(assessment, dict):
            log(f"Rejecting non-dict assessment item: {assessment!r}")
            rejected.append((index, f"assessments[{index}]: must be a JSON object, got {type(assessment).__name__}"))
            continue
        entity = assessment.get("entity")
        verdict = assessment.get("verdict")
        evidence = assessment.get("evidence", "")
        if not isinstance(entity, str) or not entity:
            log(f"Rejecting assessment with non-string entity: {assessment!r}")
            rejected.append((index, f"assessments[{index}]: must include a non-empty string `entity`"))
            continue
        if not isinstance(verdict, str) or verdict not in _ALLOWED_VERDICTS:
            log(f"Rejecting invalid assessment verdict: {assessment}")
            rejected.append(
                (index, f"assessments[{index}] ({entity}): verdict must be one of {sorted(_ALLOWED_VERDICTS)}, got {verdict!r}")
            )
            continue
        if not isinstance(evidence, str):
            evidence = str(evidence)
        valid.append((index, entity, verdict, evidence))

    # A recorded verdict is final for its pair, so when one batch gives a new
    # pair two different verdicts, recording either would silently settle the
    # disagreement. Reject every item for that pair instead and let the caller
    # resend the one it means. Repeats of the same verdict are plain duplicates,
    # and a pair recorded before this batch stays a duplicate whatever it says.
    verdicts_by_entity = {}
    for _index, entity, verdict, _evidence in valid:
        verdicts_by_entity.setdefault(entity, set()).add(verdict)
    conflicting = {
        entity for entity, verdicts in verdicts_by_entity.items() if len(verdicts) > 1 and (session_id, entity) not in existing_keys
    }

    written = 0
    duplicates = 0
    for index, entity, verdict, evidence in valid:
        key = (session_id, entity)
        if entity in conflicting:
            others = ", ".join(repr(other) for other in sorted(verdicts_by_entity[entity] - {verdict}))
            log(f"Rejecting conflicting influence assessment: session_id={session_id} entity={entity} verdict={verdict}")
            rejected.append(
                (
                    index,
                    f"assessments[{index}] ({entity}): verdict {verdict!r} conflicts with {others}"
                    " for the same entity in this batch; send only the one you mean",
                )
            )
            continue
        if key in existing_keys:
            log(f"Skipping duplicate influence assessment: session_id={session_id} entity={entity}")
            duplicates += 1
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

    log(f"Wrote {written} influence record(s) for session {session_id}; {duplicates} duplicate(s), {len(rejected)} rejected")
    summary = f"Recorded {written} influence assessment(s)."
    if duplicates:
        summary += f" Skipped {duplicates} already recorded."
    print(summary)
    if rejected:
        print(f"Error: rejected {len(rejected)} assessment(s); nothing was recorded for them:", file=sys.stderr)
        for _index, reason in sorted(rejected):
            print(f"  - {reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
