#!/usr/bin/env python3
"""Retrieve and output entities for the agent to use as extra context."""

import json
import os
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
from entity_io import find_entities_dir, get_evolve_dir, markdown_to_entity, log as _log  # noqa: E402
import audit  # noqa: E402


def log(message):
    _log("retrieve", message)


log("Script started")


def format_entities(entities):
    """Format all entities for the agent to review.

    Entities that came from a subscribed source have their path recorded in
    the private ``_source`` key (set by load_entities_with_source). These are
    annotated with ``[from: {name}]`` so the agent knows their provenance.
    """
    header = """## Evolve entities for this task

Review these stored entities and apply any that are relevant to the user's request:

"""
    items = []
    for entity in entities:
        content = entity.get("content")
        if not content:
            continue
        source = entity.get("_source")
        if source:
            content = f"[from: {source}] {content}"
        item = f"- **[{entity.get('type', 'general')}]** {content}"
        if entity.get("rationale"):
            item += f"\n  Rationale: {entity['rationale']}"
        if entity.get("trigger"):
            item += f"\n  When: {entity['trigger']}"
        items.append(item)

    return header + "\n".join(items)


def load_entities_with_source(entities_dir):
    """Load markdown entities from one recall root and annotate subscribed content.

    Symlinks and any files inside a ``.git`` directory are skipped so we don't
    surface git's own bookkeeping or sneak past path validation when a write
    -scope clone lives under entities/subscribed/{name}/.
    """
    entities_dir = Path(entities_dir)
    entities = []
    for md in sorted(p for p in entities_dir.glob("**/*.md") if ".git" not in p.parts):
        if md.is_symlink():
            continue
        try:
            entity = markdown_to_entity(md)
        except (OSError, UnicodeError):
            continue
        if not entity.get("content"):
            continue

        entity.pop("_source", None)
        entity["_id"] = str(md.relative_to(entities_dir).with_suffix(""))
        parts = md.relative_to(entities_dir).parts
        if parts and parts[0] == "subscribed" and len(parts) > 1:
            entity["_source"] = parts[1]

        entities.append(entity)

    return entities


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

    entities_dir = find_entities_dir()
    log(f"Entities dir: {entities_dir}")

    entities = []
    if entities_dir:
        entities = load_entities_with_source(entities_dir)

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
            session_id = Path(transcript_path).stem.removeprefix("claude-transcript_")
        elif isinstance(input_data.get("session_id"), str):
            session_id = input_data["session_id"]
        entity_ids = sorted({entity["_id"] for entity in entities if entity.get("_id")})
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
