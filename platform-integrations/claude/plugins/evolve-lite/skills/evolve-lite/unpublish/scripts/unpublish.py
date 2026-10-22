#!/usr/bin/env python3
"""Use the shared, owner-checked publish lifecycle implementation."""

import runpy
import sys
from pathlib import Path

skills = Path(__file__).resolve().parents[2]
for name in ("publish", "evolve-lite-publish", "evolve-lite:publish"):
    script = skills / name / "scripts" / "publish.py"
    if script.is_file():
        sys.argv.append("--unpublish")
        runpy.run_path(str(script), run_name="__main__")
        break
else:
    raise SystemExit("Cannot find installed publish script")
