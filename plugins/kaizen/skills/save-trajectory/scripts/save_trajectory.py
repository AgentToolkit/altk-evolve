#!/usr/bin/env python3
"""
Save Trajectory Script
Reads a trajectory JSON from stdin and writes it to the trajectories directory.
"""

import datetime
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path


def _get_log_dir():
    """Get user-scoped log directory with restrictive permissions."""
    try:
        uid = os.getuid()
    except AttributeError:
        uid = getpass.getuser()
    log_dir = os.path.join(tempfile.gettempdir(), f"kaizen-{uid}")
    os.makedirs(log_dir, mode=0o700, exist_ok=True)
    return log_dir


LOG_FILE = os.path.join(_get_log_dir(), "kaizen-plugin.log")


def log(message):
    """Append a timestamped message to the log file."""
    if not os.environ.get("KAIZEN_DEBUG"):
        return
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] [save-trajectory] {message}\n")


log("Script started")


def get_trajectories_dir():
    """Get the trajectories output directory, creating it if needed."""
    project_root = os.environ.get("CLAUDE_PROJECT_ROOT", "")
    if project_root:
        base = Path(project_root) / ".kaizen" / "trajectories"
    else:
        base = Path(".kaizen") / "trajectories"

    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def generate_filename(trajectories_dir):
    """Generate a timestamped filename, adding a suffix on collision."""
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    base_name = f"trajectory_{now}"

    candidate = trajectories_dir / f"{base_name}.json"
    if not candidate.exists():
        return candidate

    # Handle collisions with _1, _2, etc.
    suffix = 1
    while True:
        candidate = trajectories_dir / f"{base_name}_{suffix}.json"
        if not candidate.exists():
            return candidate
        suffix += 1


def main():
    # Read trajectory JSON from file argument or stdin
    input_path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        if input_path:
            log(f"Reading trajectory from file: {input_path}")
            with open(input_path, "r", encoding="utf-8") as f:
                trajectory = json.load(f)
        else:
            log("Reading trajectory from stdin")
            trajectory = json.load(sys.stdin)
        log(f"Received trajectory with keys: {list(trajectory.keys())}")
    except json.JSONDecodeError as e:
        log(f"Failed to parse JSON input: {e}")
        print(f"Error: Invalid JSON input - {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Input file not found - {input_path}", file=sys.stderr)
        sys.exit(1)

    messages = trajectory.get("messages", [])
    if not messages:
        log("No messages in trajectory")
        print("No messages in trajectory.", file=sys.stderr)
        sys.exit(1)

    log(f"Trajectory has {len(messages)} messages")

    # Determine output path
    trajectories_dir = get_trajectories_dir()
    output_path = generate_filename(trajectories_dir)

    # Write formatted JSON
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(trajectory, f, indent=2, default=str)
            f.write("\n")
        log(f"Wrote trajectory to {output_path}")
    except OSError as e:
        log(f"Failed to write trajectory: {e}")
        print(f"Error: Failed to write file - {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Trajectory saved: {output_path}")
    print(f"Messages: {len(messages)}")


if __name__ == "__main__":
    main()
