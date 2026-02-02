#!/usr/bin/env python3
"""Retrieve and output entities for Claude to filter."""

import json
import os
import sys
from pathlib import Path
import datetime

# Debug logging
LOG_FILE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "kaizen-plugin.log")

def log(message):
    """Append a timestamped message to the log file."""
    if not os.environ.get("KAIZEN_DEBUG"):
        return
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] [retrieve] {message}\n")

log("Script started")

# Log all environment variables
log("=== Environment Variables ===")
for key, value in sorted(os.environ.items()):
    # Mask sensitive values
    if any(sensitive in key.upper() for sensitive in ['PASSWORD', 'SECRET', 'TOKEN', 'KEY', 'API']):
        log(f"  {key}=***MASKED***")
    else:
        log(f"  {key}={value}")
log("=== End Environment Variables ===")

# Log command-line arguments
log("=== Command-Line Arguments ===")
log(f"  sys.argv: {sys.argv}")
log(f"  Script path: {sys.argv[0] if sys.argv else 'N/A'}")
log(f"  Arguments: {sys.argv[1:] if len(sys.argv) > 1 else 'None'}")
log("=== End Command-Line Arguments ===")


def find_entities_file():
    """Find the entities file in common locations."""
    locations = [
        os.environ.get("ENTITIES_FILE"),
        # Project root from Claude Code
        os.path.join(os.environ.get("CLAUDE_PROJECT_ROOT", ""), ".claude/entities.json"),
        # Current working directory
        ".claude/entities.json",
        # Plugin-relative path (fallback)
        str(Path(__file__).parent.parent / "entities.json"),
    ]
    for loc in locations:
        if loc and Path(loc).exists():
            return Path(loc)
    return None


def load_entities():
    """Load entities from the entities file."""
    entities_file = find_entities_file()
    if not entities_file:
        return []
    try:
        with open(entities_file) as f:
            data = json.load(f)
        return data.get("entities", [])
    except (json.JSONDecodeError, IOError):
        return []


def format_entities(entities):
    """Format all entities for Claude to review."""
    header = """## Entities for this task

Review these entities and apply any relevant ones:

"""
    items = []
    for e in entities:
        content = e.get('content')
        if not content:
            continue
        item = f"- **[{e.get('category', 'general')}]** {content}"
        if e.get('rationale'):
            item += f"\n  - _Rationale: {e['rationale']}_"
        if e.get('trigger'):
            item += f"\n  - _When: {e['trigger']}_"
        items.append(item)

    return header + "\n".join(items)


def main():
    # Read input from stdin (hook provides JSON with prompt)
    try:
        input_data = json.load(sys.stdin)
        log("=== Input Data ===")
        log(f"  Keys: {list(input_data.keys())}")
        log(f"  Full content: {json.dumps(input_data, indent=2)}")
        log("=== End Input Data ===")
    except json.JSONDecodeError as e:
        log(f"Failed to parse JSON input: {e}")
        return

    # Load all entities
    entities_file = find_entities_file()
    log(f"Entities file: {entities_file}")

    entities = load_entities()
    if not entities:
        log("No entities found")
        return

    log(f"Loaded {len(entities)} entities")

    # Output all entities - Claude will filter for relevance
    output = format_entities(entities)
    print(output)
    log(f"Output {len(output)} chars to stdout")


if __name__ == "__main__":
    main()
