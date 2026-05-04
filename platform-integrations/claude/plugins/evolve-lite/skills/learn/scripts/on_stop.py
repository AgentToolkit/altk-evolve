#!/usr/bin/env python3
"""Stop hook that triggers the learn skill to extract guidelines."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from entity_io import get_trajectories_dir  # noqa: E402


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        input_data = {}

    if input_data.get("stop_hook_active") is True:
        return

    transcript_path = input_data.get("transcript_path", "")
    reason = "Run the /evolve-lite:learn skill."
    if transcript_path:
        session_id = Path(transcript_path).stem.removeprefix("claude-transcript_")
        if session_id:
            saved_trajectory = str(get_trajectories_dir() / f"claude-transcript_{session_id}.jsonl")
            reason += f" The saved trajectory path is: {saved_trajectory}"

    print(
        json.dumps(
            {
                "decision": "block",
                "reason": reason,
                "suppressOutput": True,
                "systemMessage": "Running the evolve-lite learn skill...",
            }
        )
    )


if __name__ == "__main__":
    main()
