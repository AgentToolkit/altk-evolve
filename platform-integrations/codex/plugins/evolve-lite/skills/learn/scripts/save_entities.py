#!/usr/bin/env python3
"""
Save Entities Script
Reads entities from stdin JSON and writes each as a markdown file
in the entities directory, organized by type.
"""

import json
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
from entity_io import (  # noqa: E402
    find_entities_dir,
    get_default_entities_dir,
    load_all_entities,
    write_entity_file,
    log as _log,
)


def log(message):
    _log("save", message)


log("Script started")


def normalize(text):
    """Normalize content for dedup comparison."""
    return " ".join(text.lower().split())


def main():
    try:
        input_data = json.load(sys.stdin)
        log(f"Received input with keys: {list(input_data.keys())}")
    except json.JSONDecodeError as e:
        log(f"Failed to parse JSON input: {e}")
        print(f"Error: Invalid JSON input - {e}", file=sys.stderr)
        sys.exit(1)

    new_entities = input_data.get("entities", [])
    if not new_entities:
        log("No entities in input")
        print("No entities provided in input.", file=sys.stderr)
        sys.exit(0)

    log(f"Received {len(new_entities)} new entities")

    entities_dir = find_entities_dir()
    if entities_dir:
        entities_dir = entities_dir.resolve()
        log(f"Found existing dir: {entities_dir}")
        print(f"Using existing entities dir: {entities_dir}")
    else:
        entities_dir = get_default_entities_dir()
        log(f"Created new dir: {entities_dir}")
        print(f"Created new entities dir: {entities_dir}")

    existing_entities = load_all_entities(entities_dir)
    existing_contents = {normalize(e["content"]) for e in existing_entities if e.get("content")}
    log(f"Existing entities: {len(existing_entities)}")

    added_count = 0
    for entity in new_entities:
        content = entity.get("content")
        if not content:
            log(f"Skipping entity without content: {entity}")
            continue
        if normalize(content) in existing_contents:
            log(f"Skipping duplicate: {content[:60]}")
            continue

        path = write_entity_file(entities_dir, entity)
        existing_contents.add(normalize(content))
        added_count += 1
        log(f"Wrote: {path}")

    total = len(existing_entities) + added_count
    log(f"Added {added_count} new entities. Total: {total}")
    print(f"Added {added_count} new entity(ies). Total: {total}")
    print(f"Entities stored in: {entities_dir}")


if __name__ == "__main__":
    main()
