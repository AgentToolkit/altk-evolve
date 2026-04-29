#!/usr/bin/env python3
"""
Save Trajectory Script
Reads a trajectory JSON from a file path argument (or stdin as fallback)
and writes it to the .evolve/trajectories/ directory.
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
        log_dir = os.path.join(tempfile.gettempdir(), f"evolve-{uid}")
        os.makedirs(log_dir, mode=0o700, exist_ok=True)
        _log_file = os.path.join(log_dir, "evolve-plugin.log")
    return _log_file


def log(message):
    """Append a timestamped message to the log file. Best-effort; never raises."""
    if not os.environ.get("EVOLVE_DEBUG"):
        return
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_get_log_file(), "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [save-trajectory] {message}\n")
    except Exception:
        pass


def get_trajectories_dir():
    """Get the trajectories output directory, creating it if needed.

    Resolution order:
      1. ``EVOLVE_DIR`` env var (matches the documented contract)
      2. ``CLAUDE_PROJECT_ROOT`` env var (the agent's project root)
      3. ``.evolve/`` in the current working directory
    """
    evolve_dir = os.environ.get("EVOLVE_DIR")
    if evolve_dir:
        base = Path(evolve_dir) / "trajectories"
    else:
        project_root = os.environ.get("CLAUDE_PROJECT_ROOT", "")
        if project_root:
            base = Path(project_root) / ".evolve" / "trajectories"
        else:
            base = Path(".evolve") / "trajectories"

    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base.resolve()


def open_trajectory_file(trajectories_dir):
    """Atomically claim a timestamped trajectory file.

    Returns a ``(Path, fd)`` tuple. Uses ``O_CREAT | O_EXCL`` so two saves
    racing within the same second pick distinct filenames instead of one
    overwriting the other.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    base_name = f"trajectory_{now}"

    for suffix in range(0, 1000):
        name = f"{base_name}.json" if suffix == 0 else f"{base_name}_{suffix}.json"
        candidate = trajectories_dir / name
        try:
            fd = os.open(str(candidate), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            return candidate, fd
        except FileExistsError:
            continue

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
    messages = trajectory.get("messages")
    if not isinstance(messages, list) or not messages:
        log(f"Invalid messages in trajectory: {type(messages).__name__}")
        print("Error: `messages` must be a non-empty list.", file=sys.stderr)
        sys.exit(1)

    log(f"Trajectory has {len(messages)} messages")

    # Atomically claim a unique output path (handles same-second races)
    trajectories_dir = get_trajectories_dir()
    output_path, fd = open_trajectory_file(trajectories_dir)

    # Write formatted JSON via the already-opened owner-only fd
    try:
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
