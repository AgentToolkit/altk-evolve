#!/usr/bin/env python3
"""Retrieve and output guidelines for Claude to filter."""

import json
import os
import sys
from pathlib import Path
import datetime

# Debug logging
LOG_FILE = "/tmp/guidelines-plugin.log"

def log(message):
    """Append a timestamped message to the log file."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] [retrieve] {message}\n")

log("Script started")


def find_guidelines_file():
    """Find the guidelines file in common locations."""
    locations = [
        os.environ.get("GUIDELINES_FILE"),
        # Project root from Claude Code
        os.path.join(os.environ.get("CLAUDE_PROJECT_ROOT", ""), ".claude/guidelines.json"),
        # Current working directory
        ".claude/guidelines.json",
        # Plugin-relative path (fallback)
        str(Path(__file__).parent.parent / "guidelines.json"),
    ]
    for loc in locations:
        if loc and Path(loc).exists():
            return Path(loc)
    return None


def load_guidelines():
    """Load guidelines from the guidelines file."""
    guidelines_file = find_guidelines_file()
    if not guidelines_file:
        return []
    try:
        with open(guidelines_file) as f:
            data = json.load(f)
        return data.get("guidelines", [])
    except (json.JSONDecodeError, IOError):
        return []


def format_guidelines(guidelines):
    """Format all guidelines for Claude to review."""
    header = """## Guidelines for this task

Review these guidelines and apply any relevant ones:

"""
    items = []
    for g in guidelines:
        content = g.get('content')
        if not content:
            continue
        item = f"- **[{g.get('category', 'general')}]** {content}"
        if g.get('rationale'):
            item += f"\n  - _Rationale: {g['rationale']}_"
        if g.get('trigger'):
            item += f"\n  - _When: {g['trigger']}_"
        items.append(item)

    return header + "\n".join(items)


def main():
    # Read input from stdin (hook provides JSON with prompt)
    try:
        input_data = json.load(sys.stdin)
        log(f"Received input with keys: {list(input_data.keys())}")
    except json.JSONDecodeError as e:
        log(f"Failed to parse JSON input: {e}")
        return

    # Load all guidelines
    guidelines_file = find_guidelines_file()
    log(f"Guidelines file: {guidelines_file}")

    guidelines = load_guidelines()
    if not guidelines:
        log("No guidelines found")
        return

    log(f"Loaded {len(guidelines)} guidelines")

    # Output all guidelines - Claude will filter for relevance
    output = format_guidelines(guidelines)
    print(output)
    log(f"Output {len(output)} chars to stdout")


if __name__ == "__main__":
    main()
