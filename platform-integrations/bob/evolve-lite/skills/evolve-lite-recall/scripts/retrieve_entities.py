#!/usr/bin/env python3
"""Retrieve and output an entity manifest for bob to expand on demand."""

import json
import os
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
from entity_io import dedupe_manifest_entries, find_recall_entity_dirs, get_evolve_dir, load_manifest, log as _log  # noqa: E402
import audit  # noqa: E402


def log(message):
    _log("retrieve", message)


log("Script started")


def format_entities(entities):
    """Format a manifest of entities for bob to expand on demand."""
    header = """## Evolve entity manifest for this task

These stored entities are available for this repo. Read only the files whose trigger looks relevant to the user's request:

"""
    lines = [f"- `{e['path']}` [{e['type']}] — {e['trigger']}" for e in entities]
    return header + "\n".join(lines)


def _audit_id(path_str):
    """Derive the audit entity id from a manifest path.

    Matches upstream's convention for entities/: id is the path relative to
    ``entities/`` with ``.md`` stripped (e.g. ``guideline/foo``,
    ``subscribed/alice/guideline/bar``). Public entities are prefixed with
    ``public/`` to keep the id space distinct from private entities.
    """
    if "/entities/" in path_str:
        return path_str.split("/entities/", 1)[1].removesuffix(".md")
    if "/public/" in path_str:
        return "public/" + path_str.split("/public/", 1)[1].removesuffix(".md")
    return path_str.removesuffix(".md")


def main():
    # Hook context arrives via stdin as JSON when invoked from a hook
    # (claude/claw-code/codex). Handle empty/absent stdin gracefully so the
    # script also works when invoked manually (no hook upstream).
    input_data = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            input_data = json.loads(raw)
            if isinstance(input_data, dict):
                log(f"Input keys: {list(input_data.keys())}")
            else:
                log(f"Input type: {type(input_data).__name__}")
        else:
            log("stdin was empty")
    except json.JSONDecodeError as e:
        log(f"stdin was not valid JSON ({e})")
        return

    if isinstance(input_data, dict):
        prompt = input_data.get("prompt", "")
        if prompt:
            log(f"Prompt preview: {prompt[:120]}")

    log("=== Environment Variables ===")
    for key, value in sorted(os.environ.items()):
        if any(sensitive in key.upper() for sensitive in ["PASSWORD", "SECRET", "TOKEN", "KEY", "API"]):
            log(f"  {key}=***MASKED***")
        else:
            log(f"  {key}={value}")
    log("=== End Environment Variables ===")

    entities = []
    recall_dirs = find_recall_entity_dirs()
    log(f"Recall dirs: {recall_dirs}")
    for root_dir in recall_dirs:
        entities.extend(load_manifest(root_dir))

    entities = dedupe_manifest_entries(entities)

    if not entities:
        log("No entities found")
        return

    log(f"Loaded {len(entities)} entities")

    output = format_entities(entities)
    print(output)
    log(f"Output {len(output)} chars to stdout")

    # Audit which entity ids were served to this session. Logging is
    # intentionally best-effort so recall never fails because provenance
    # recording could not append to audit.log.
    try:
        if isinstance(input_data, dict):
            transcript_path = input_data.get("transcript_path", "")
        else:
            transcript_path = ""
        session_id = None
        if transcript_path:
            stem = Path(transcript_path).stem
            if stem.startswith("claude-transcript_"):
                session_id = stem.removeprefix("claude-transcript_")
        if not session_id and isinstance(input_data, dict) and isinstance(input_data.get("session_id"), str):
            session_id = input_data["session_id"]
        entity_ids = sorted({_audit_id(entity["path"]) for entity in entities if entity.get("path")})
        if session_id and entity_ids:
            audit.append(
                evolve_dir=str(get_evolve_dir().resolve()),
                event="recall",
                session_id=session_id,
                entities=entity_ids,
            )
            log(f"Audit: recall session_id={session_id} entities={len(entity_ids)}")
    except Exception as exc:
        log(f"Audit append failed (non-fatal): {exc}")


if __name__ == "__main__":
    main()
