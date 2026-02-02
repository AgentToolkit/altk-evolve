#!/usr/bin/env python3
"""
Save Entities Script
Reads entities from stdin and appends them to the entities file.
"""

import json
import os
import sys
from pathlib import Path
import datetime

# Debug logging - use user-scoped directory for security
import tempfile

def _get_log_dir():
    """Get user-scoped log directory with restrictive permissions."""
    log_dir = os.path.join(tempfile.gettempdir(), f"kaizen-{os.getuid()}")
    os.makedirs(log_dir, mode=0o700, exist_ok=True)
    return log_dir

LOG_FILE = os.path.join(_get_log_dir(), "kaizen-plugin.log")

def log(message):
    """Append a timestamped message to the log file."""
    if not os.environ.get("KAIZEN_DEBUG"):
        return
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] [save] {message}\n")

log("Script started")


def find_entities_file():
    """Find existing entities file, checking multiple locations."""
    # If ENTITIES_FILE is explicitly set, honor it even if the file doesn't exist yet
    env_val = os.environ.get("ENTITIES_FILE")
    if env_val:
        return Path(env_val).resolve()

    # Fall back to checking other candidate locations
    locations = [
        # Project root from Claude Code
        os.path.join(os.environ.get("CLAUDE_PROJECT_ROOT", ""), ".claude/entities.json"),
        # Current working directory
        ".claude/entities.json",
        # Plugin-relative path (fallback)
        str(Path(__file__).parent.parent / "entities.json"),
    ]

    for loc in locations:
        if loc and Path(loc).exists():
            return Path(loc).resolve()

    return None


def get_default_entities_path():
    """Get default path for new entities file."""
    # Prefer project root if available
    project_root = os.environ.get("CLAUDE_PROJECT_ROOT", "")
    if project_root:
        claude_dir = Path(project_root) / ".claude"
    else:
        # Fall back to current directory's .claude/
        claude_dir = Path(".claude")

    claude_dir.mkdir(parents=True, exist_ok=True)
    return (claude_dir / "entities.json").resolve()


def load_existing_entities(path):
    """Load existing entities from file."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("entities", [])
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_entities(path, entities):
    """Save entities to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"entities": entities}, f, indent=2)
        f.write("\n")


def main():
    # Read entities from stdin
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

    # Find or create entities file
    existing_path = find_entities_file()

    if existing_path:
        entities_path = existing_path
        existing_entities = load_existing_entities(entities_path)
        log(f"Found existing file: {entities_path} with {len(existing_entities)} entities")
        print(f"Appending to existing file: {entities_path}")
    else:
        entities_path = get_default_entities_path()
        existing_entities = []
        log(f"Creating new file: {entities_path}")
        print(f"Creating new file: {entities_path}")

    # Merge entities (avoid duplicates by content)
    existing_contents = {e.get("content") for e in existing_entities if e.get("content")}
    added_count = 0

    for entity in new_entities:
        content = entity.get("content")
        if not content:
            log(f"Skipping entity without content: {entity}")
            continue
        if content not in existing_contents:
            existing_entities.append(entity)
            existing_contents.add(content)
            added_count += 1

    # Save merged entities
    save_entities(entities_path, existing_entities)

    log(f"Added {added_count} new entities. Total: {len(existing_entities)}")
    print(f"Added {added_count} new entity(ies). Total: {len(existing_entities)}")
    print(f"Entities stored in: {entities_path}")


if __name__ == "__main__":
    main()
