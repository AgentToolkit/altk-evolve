#!/usr/bin/env python3
"""
Save Trajectory Script
Reads a trajectory JSON from a file path argument (or stdin as fallback)
and writes it to the .kaizen/trajectories/ directory.
"""

import datetime
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path


_log_file = None


def _get_log_file():
    """Get log file path, lazily creating the log directory on first use."""
    global _log_file
    if _log_file is None:
        try:
            uid = os.getuid()
        except AttributeError:
            uid = getpass.getuser()
        log_dir = os.path.join(tempfile.gettempdir(), f"kaizen-{uid}")
        os.makedirs(log_dir, mode=0o700, exist_ok=True)
        _log_file = os.path.join(log_dir, "kaizen-plugin.log")
    return _log_file


def log(message):
    """Append a timestamped message to the log file. Best-effort; never raises."""
    if not os.environ.get("KAIZEN_DEBUG"):
        return
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_get_log_file(), "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [save-trajectory] {message}\n")
    except Exception:
        pass


def get_trajectories_dir():
    """Get the trajectories output directory, creating it if needed."""
    project_root = os.environ.get("CLAUDE_PROJECT_ROOT", "")
    if project_root:
        base = Path(project_root) / ".kaizen" / "trajectories"
    else:
        base = Path(".kaizen") / "trajectories"

    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base.resolve()


def generate_filename(trajectories_dir):
    """Generate a timestamped filename, adding a suffix on collision."""
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    base_name = f"trajectory_{now}"

    candidate = trajectories_dir / f"{base_name}.json"
    if not candidate.exists():
        return candidate

    # Handle collisions with _1, _2, etc.
    for suffix in range(1, 1000):
        candidate = trajectories_dir / f"{base_name}_{suffix}.json"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Too many trajectory files for timestamp {now}")


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
    except json.JSONDecodeError as e:
        log(f"Failed to parse JSON input: {e}")
        print(f"Error: Invalid JSON input - {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        log(f"Failed to read input: {e}")
        print(f"Error: Failed to read input - {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(trajectory, dict):
        log(f"Expected JSON object, got {type(trajectory).__name__}")
        print(f"Error: Expected JSON object, got {type(trajectory).__name__}", file=sys.stderr)
        sys.exit(1)

    log(f"Received trajectory with keys: {list(trajectory.keys())}")
    messages = trajectory.get("messages", [])
    if not messages:
        log("No messages in trajectory")
        print("No messages in trajectory.", file=sys.stderr)
        sys.exit(1)

    log(f"Trajectory has {len(messages)} messages")

    # Determine output path
    trajectories_dir = get_trajectories_dir()
    output_path = generate_filename(trajectories_dir)

    # Write formatted JSON with owner-only permissions
    try:
        fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
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
