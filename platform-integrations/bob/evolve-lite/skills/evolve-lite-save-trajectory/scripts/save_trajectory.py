#!/usr/bin/env python3
"""
Save Trajectory Script
Reads trajectory JSON from stdin and writes it to .evolve/trajectories/
with a timestamped filename.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def find_evolve_dir():
    """Walk up from CWD to find an existing .evolve/ directory, or return default."""
    cwd = Path.cwd()
    for ancestor in [cwd] + list(cwd.parents):
        candidate = ancestor / ".evolve"
        if candidate.is_dir():
            return candidate
    return cwd / ".evolve"


def main():
    """Read trajectory JSON from stdin and write to a timestamped file."""
    try:
        trajectory = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input - {e}", file=sys.stderr)
        sys.exit(1)

    evolve_dir = find_evolve_dir()
    trajectories_dir = evolve_dir / "trajectories"
    trajectories_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    base = f"trajectory_{timestamp}"

    # Atomic create with collision avoidance for same-second writes
    n = 0
    while True:
        filename = f"{base}.json" if n == 0 else f"{base}_{n}.json"
        output_path = trajectories_dir / filename
        try:
            fd = os.open(str(output_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            n += 1

    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(trajectory, f, indent=2, ensure_ascii=False)

    try:
        rel_path = output_path.relative_to(evolve_dir.parent)
    except ValueError:
        rel_path = output_path

    messages = len(trajectory.get("messages", []))
    print(f"Trajectory saved: {rel_path}")
    print(f"Messages: {messages}")


if __name__ == "__main__":
    main()
