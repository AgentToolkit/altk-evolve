#!/usr/bin/env python3
"""Retrieve and output entities for Codex to use as extra developer context."""

import json
import os
import sys
from pathlib import Path

# Walk up from the script location to find the installed plugin lib directory.
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    for _candidate in (
        _ancestor / "lib",
        _ancestor / "platform-integrations" / "claude" / "plugins" / "evolve-lite" / "lib",
    ):
        if (_candidate / "entity_io.py").is_file():
            _lib = _candidate
            break
    if _lib is not None:
        break
if _lib is None:
    raise ImportError(f"Cannot find plugin lib directory above {_script}")
sys.path.insert(0, str(_lib))
from entity_io import find_entities_dir, markdown_to_entity, log as _log  # noqa: E402


def log(message):
    _log("retrieve", message)


log("Script started")


def format_entities(entities):
    """Format all entities for Codex to review."""
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
    """Load markdown entities from one recall root and annotate subscribed content."""
    entities_dir = Path(entities_dir)
    entities = []
    for md in sorted(entities_dir.glob("**/*.md")):
        if md.is_symlink():
            continue
        try:
            entity = markdown_to_entity(md)
        except (OSError, UnicodeError):
            continue
        if not entity.get("content"):
            continue

        entity.pop("_source", None)
        parts = md.relative_to(entities_dir).parts
        if parts and parts[0] == "subscribed" and len(parts) > 1:
            entity["_source"] = parts[1]

        entities.append(entity)

    return entities


def main():
    try:
        input_data = json.load(sys.stdin)
        log(f"Input keys: {list(input_data.keys())}")
    except json.JSONDecodeError as e:
        log(f"Failed to parse JSON input: {e}")
        return

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


if __name__ == "__main__":
    main()
