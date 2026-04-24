#!/usr/bin/env python3
"""Stop hook that triggers the learn skill to extract guidelines."""

import json
import sys


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        input_data = {}

    if input_data.get("stop_hook_active") is True:
        return

    print(
        json.dumps(
            {
                "decision": "block",
                "reason": "Run the /evolve-lite:learn skill to extract guidelines from this conversation.",
                "suppressOutput": True,
                "systemMessage": "Running the evolve-lite learn skill...",
            }
        )
    )


if __name__ == "__main__":
    main()
