#!/usr/bin/env python3
"""Stop hook that copies the session transcript to .evolve/trajectories/."""

import datetime
import getpass
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from entity_io import get_trajectories_dir  # noqa: E402


_log_file = None


def _get_log_file():
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
    if not os.environ.get("EVOLVE_DEBUG"):
        return
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_get_log_file(), "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [save-trajectory-stop] {message}\n")
    except Exception:
        pass


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        input_data = {}

    log(f"Stop hook input keys: {list(input_data.keys())}")
    log(f"Stop hook input: {json.dumps(input_data, default=str)[:2000]}")

    transcript_path = input_data.get("transcript_path")
    if not transcript_path:
        log("No transcript_path in stop hook input")
        return

    src = Path(transcript_path)
    if not src.is_file():
        log(f"Transcript file not found: {src}")
        return

    session_id = src.stem
    trajectories_dir = get_trajectories_dir()
    dst = trajectories_dir / f"claude-transcript_{session_id}.jsonl"

    shutil.copy2(str(src), str(dst))
    log(f"Copied transcript {src} -> {dst}")
    print(f"Trajectory saved: {dst}")


if __name__ == "__main__":
    main()
