#!/usr/bin/env python3
"""Retrieve and output entities for Claude to filter."""

import getpass
import json
import os
import sys
from pathlib import Path
import datetime
import tempfile


def _get_log_dir():
    """Get user-scoped log directory with restrictive permissions."""
    try:
        uid = os.getuid()
    except AttributeError:
        # Windows doesn't have os.getuid(); fall back to username
        uid = getpass.getuser()
    log_dir = os.path.join(tempfile.gettempdir(), f"kaizen-{uid}")
    os.makedirs(log_dir, mode=0o700, exist_ok=True)
    return log_dir


# Debug logging - use user-scoped directory for security
LOG_FILE = os.path.join(_get_log_dir(), "kaizen-plugin.log")

def log(message):
    """Append a timestamped message to the log file."""
    if not os.environ.get("KAIZEN_DEBUG"):
        return
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
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
    # If KAIZEN_ENTITIES_FILE env var is set, it is authoritative - no fallbacks
    entities_file_env = os.environ.get("KAIZEN_ENTITIES_FILE")
    if entities_file_env:
        path = Path(entities_file_env)
        return path if path.exists() else None

    # Fallback locations when KAIZEN_ENTITIES_FILE is not set
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
            return Path(loc)
    return None


def load_entities():
    """Load entities from the entities file.

    Returns:
        list: The entities list on success or if file not found.
        None: If the file exists but contains invalid JSON.
    """
    entities_file = find_entities_file()
    if not entities_file:
        return []
    try:
        with open(entities_file, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("entities", [])
    except IOError:
        return []
    except json.JSONDecodeError as e:
        log(f"load_entities: JSON decode error in {entities_file}: {e}")
        return None


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
    if entities is None:
        log(f"Failed to load entities due to invalid JSON in {entities_file}")
        print(f"Error: {entities_file} contains invalid JSON.", file=sys.stderr)
        return
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
