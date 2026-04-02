#!/usr/bin/env python3
"""Stop hook script: trigger the learn skill once per user turn.

If the flag file does not exist, this is the first Stop event for the
current turn. Create the flag and print an instruction that tells Claude
to invoke /evolve-lite:learn.

If the flag file already exists, learn has already been requested (or
completed). Print nothing so the turn ends normally.
"""

import json
import os
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from entity_io import auto_learn_flag_path, log as _log


def log(message):
    _log("auto_learn", message)


def main():
    # Consume stdin (Stop hook sends JSON)
    try:
        input_data = json.load(sys.stdin)
        log(f"Stop hook input: {json.dumps(input_data)}")
    except (json.JSONDecodeError, ValueError):
        log("No valid JSON on stdin")

    flag = auto_learn_flag_path()
    log(f"Flag path: {flag}")

    if os.path.exists(flag):
        log("Flag exists — learn already triggered this turn. Exiting silently.")
        return

    # Create the flag file
    try:
        with open(flag, "w") as f:
            f.write("")
        log("Flag created. Outputting learn instruction.")
    except OSError as e:
        log(f"Failed to create flag: {e}")
        return

    # Output instruction for Claude to invoke the learn skill
    print(
        "Please run the evolve-lite:learn skill now to extract and save"
        " entities from this conversation."
    )


if __name__ == "__main__":
    main()
