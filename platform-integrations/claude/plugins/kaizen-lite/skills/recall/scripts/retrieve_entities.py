#!/usr/bin/env python3
"""Retrieve and output entities for Claude to filter."""

import json
import os
import sys
from pathlib import Path

# Add lib to path so we can import entity_io
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from entity_io import find_entities_dir, load_all_entities, log as _log


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

# Log command-line arguments
log("=== Command-Line Arguments ===")
log(f"  sys.argv: {sys.argv}")
log(f"  Script path: {sys.argv[0] if sys.argv else 'N/A'}")
log(f"  Arguments: {sys.argv[1:] if len(sys.argv) > 1 else 'None'}")
log("=== End Command-Line Arguments ===")


def format_entities(entities):
    """Format all entities for Claude to review."""
    # Separate gists from other entities
    gists = [e for e in entities if e.get("type") == "gist"]
    other = [e for e in entities if e.get("type") != "gist"]

    sections = []

    if other:
        header = """## Entities for this task

Review these entities and apply any relevant ones:

"""
        items = []
        for e in other:
            content = e.get("content")
            if not content:
                continue
            item = f"- **[{e.get('type', 'general')}]** {content}"
            if e.get("rationale"):
                item += f"\n  - _Rationale: {e['rationale']}_"
            if e.get("trigger"):
                item += f"\n  - _When: {e['trigger']}_"
            items.append(item)
        sections.append(header + "\n".join(items))

    if gists:
        gist_header = """## Conversation Gists

These are gists from prior conversations, optimized for recalling user preferences and attributes:

"""
        gist_items = []
        for g in gists:
            content = g.get("content")
            if content:
                gist_items.append(f"- {content}")
        sections.append(gist_header + "\n".join(gist_items))

    return "\n\n".join(sections)


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

    # Load all entities from directory
    entities_dir = find_entities_dir()
    log(f"Entities dir: {entities_dir}")

    if not entities_dir:
        log("No entities directory found")
        return

    entities = load_all_entities(entities_dir)
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
