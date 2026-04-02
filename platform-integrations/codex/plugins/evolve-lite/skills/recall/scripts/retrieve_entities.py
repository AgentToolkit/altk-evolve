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
    _candidate = _ancestor / "lib"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
        break
if _lib is None:
    raise ImportError(f"Cannot find plugin lib directory above {_script}")
sys.path.insert(0, str(_lib))
from entity_io import find_entities_dir, load_all_entities, log as _log  # noqa: E402


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
        item = f"- **[{entity.get('type', 'general')}]** {content}"
        if entity.get("rationale"):
            item += f"\n  Rationale: {entity['rationale']}"
        if entity.get("trigger"):
            item += f"\n  When: {entity['trigger']}"
        items.append(item)

    return header + "\n".join(items)


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
    if not entities_dir:
        log("No entities directory found")
        return

    entities = load_all_entities(entities_dir)
    if not entities:
        log("No entities found")
        return

    output = format_entities(entities)
    print(output)
    log(f"Output {len(output)} chars to stdout")


if __name__ == "__main__":
    main()
