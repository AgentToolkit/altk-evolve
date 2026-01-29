#!/usr/bin/env python3
"""
Save Guidelines Script
Reads guidelines from stdin and appends them to the guidelines file.
"""

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
        f.write(f"[{timestamp}] [save] {message}\n")

log("Script started")


def find_guidelines_file():
    """Find existing guidelines file, checking multiple locations."""
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
            return Path(loc).resolve()

    return None


def get_default_guidelines_path():
    """Get default path for new guidelines file."""
    # Prefer project root if available
    project_root = os.environ.get("CLAUDE_PROJECT_ROOT", "")
    if project_root:
        claude_dir = Path(project_root) / ".claude"
    else:
        # Fall back to current directory's .claude/
        claude_dir = Path(".claude")

    claude_dir.mkdir(parents=True, exist_ok=True)
    return (claude_dir / "guidelines.json").resolve()


def load_existing_guidelines(path):
    """Load existing guidelines from file."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("guidelines", [])
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_guidelines(path, guidelines):
    """Save guidelines to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"guidelines": guidelines}, f, indent=2)
        f.write("\n")


def main():
    # Read guidelines from stdin
    try:
        input_data = json.load(sys.stdin)
        log(f"Received input with keys: {list(input_data.keys())}")
    except json.JSONDecodeError as e:
        log(f"Failed to parse JSON input: {e}")
        print(f"Error: Invalid JSON input - {e}", file=sys.stderr)
        sys.exit(1)

    new_guidelines = input_data.get("guidelines", [])
    if not new_guidelines:
        log("No guidelines in input")
        print("No guidelines provided in input.", file=sys.stderr)
        sys.exit(0)

    log(f"Received {len(new_guidelines)} new guidelines")

    # Find or create guidelines file
    existing_path = find_guidelines_file()

    if existing_path:
        guidelines_path = existing_path
        existing_guidelines = load_existing_guidelines(guidelines_path)
        log(f"Found existing file: {guidelines_path} with {len(existing_guidelines)} guidelines")
        print(f"Appending to existing file: {guidelines_path}")
    else:
        guidelines_path = get_default_guidelines_path()
        existing_guidelines = []
        log(f"Creating new file: {guidelines_path}")
        print(f"Creating new file: {guidelines_path}")

    # Merge guidelines (avoid duplicates by content)
    existing_contents = {g.get("content") for g in existing_guidelines if g.get("content")}
    added_count = 0

    for guideline in new_guidelines:
        content = guideline.get("content")
        if not content:
            log(f"Skipping guideline without content: {guideline}")
            continue
        if content not in existing_contents:
            existing_guidelines.append(guideline)
            existing_contents.add(content)
            added_count += 1

    # Save merged guidelines
    save_guidelines(guidelines_path, existing_guidelines)

    log(f"Added {added_count} new guidelines. Total: {len(existing_guidelines)}")
    print(f"Added {added_count} new guideline(s). Total: {len(existing_guidelines)}")
    print(f"Guidelines stored in: {guidelines_path}")


if __name__ == "__main__":
    main()
