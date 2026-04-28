#!/usr/bin/env python3
"""Retrieve and output an entity manifest for Bob to expand on demand."""

import sys
from pathlib import Path

# Smart import: walk up to find evolve-lib
current = Path(__file__).resolve()
for parent in current.parents:
    lib_path = parent / "evolve-lib"
    if lib_path.exists():
        sys.path.insert(0, str(lib_path))
        break

from entity_io import dedupe_manifest_entries, find_recall_entity_dirs, load_manifest, log as _log  # noqa: E402


def log(message):
    _log("retrieve", message)


def format_entities(entities):
    """Format a manifest of entities as human-readable markdown for Bob."""
    header = """## Evolve entity manifest for this task

These stored entities are available for this repo. Read only the files whose trigger looks relevant to the user's request:

"""
    lines = []
    for e in entities:
        lines.append(f"- `{e['path']}` [{e['type']}] \u2014 {e['trigger']}")
    return header + "\n".join(lines)


def main():
    log("Script started")

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
