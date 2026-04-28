#!/usr/bin/env python3
"""Retrieve and output an entity manifest for Claude to expand on demand."""

import json
import os
import sys
from pathlib import Path

# Add lib to path so we can import entity_io
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from entity_io import dedupe_manifest_entries, find_recall_entity_dirs, load_manifest, log as _log


def log(message):
    _log("retrieve", message)


log("Script started")

# Log all environment variables
log("=== Environment Variables ===")
for key, value in sorted(os.environ.items()):
    # Mask sensitive values
    if any(sensitive in key.upper() for sensitive in ["PASSWORD", "SECRET", "TOKEN", "KEY", "API"]):
        log(f"  {key}=***MASKED***")
    else:
        log(f"  {key}={value}")
log("=== End Environment Variables ===")


def format_entities(entities):
    """Format a manifest of entities for Claude to expand on demand."""
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
