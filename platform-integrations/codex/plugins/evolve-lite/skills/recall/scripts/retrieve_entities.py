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
from entity_io import dedupe_manifest_entries, find_recall_entity_dirs, load_manifest, log as _log  # noqa: E402


def log(message):
    _log("retrieve", message)


log("Script started")


def format_entities(entities):
    """Format a manifest of entities for Codex to expand on demand."""
    header = """## Evolve entity manifest for this task

These stored entities are available for this repo. Read only the files whose trigger looks relevant to the user's request:

"""
    return header + "\n".join(json.dumps(entity) for entity in entities)


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


if __name__ == "__main__":
    main()
