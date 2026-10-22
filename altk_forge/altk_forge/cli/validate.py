"""Validate trajectory and session files against the schemas and check that references resolve.

Usage: uv run python -m altk_forge.cli.validate <file.json> [...]
A file with `turns` is validated as a session; its turns are looked up next to it (*.trajectory.json).
"""

import argparse
import json
import sys
from pathlib import Path

from ..trajectory.spans import walk
from ..trajectory.validate import check, span_ids, validator_for


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", metavar="file.json", help="trajectories or sessions")
    paths = ap.parse_args(argv).files
    failed = False
    for path in map(Path, paths):
        doc = json.loads(path.read_text())
        is_session = "turns" in doc
        validator = validator_for(doc)
        errors = []
        for e in validator.iter_errors(doc):
            # Errors inside the recursive tree are reported at the root; descend to the most specific one.
            while e.context:
                e = max(e.context, key=lambda c: len(c.absolute_path))
            errors.append(f"{'/'.join(map(str, e.absolute_path))}: {e.message[:200]}")
        if is_session:
            turns = {}
            for f in path.parent.glob("*.trajectory.json"):
                t = json.loads(f.read_text())
                turns[t["trace_id"]] = {x["span_id"] for x in walk(t["tree"])}
            errors += [f"turn {t} not found next to the session" for t in doc["turns"] if t not in turns]
            errors += check(doc, turns)
        else:
            errors += check(doc, span_ids(doc))
        print(f"{'FAIL' if errors else 'ok  '} {path}")
        for e in errors:
            print(f"     {e}")
        failed |= bool(errors)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
