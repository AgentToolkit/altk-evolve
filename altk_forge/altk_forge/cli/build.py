"""Convert a folder of Langfuse trace exports into trajectories, then validate them.

Usage: uv run python -m altk_forge.cli.build --traces <dir> --out <dir> [--host URL] [--force]
"""

import argparse
import sys
from pathlib import Path

from ..builtin.files import build


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--traces", required=True, help="folder with *.langfuse.json")
    ap.add_argument("--out", required=True, help="folder for trajectories")
    ap.add_argument("--host", help="Langfuse host, used for links back to the trace")
    ap.add_argument("--force", action="store_true", help="rebuild trajectories that are already up to date")
    args = ap.parse_args()
    traces, out, host = Path(args.traces), Path(args.out), args.host

    built, skipped, failed = build(traces, out, host, args.force, log=lambda _: None)
    print(f"Done: {built} built, {skipped} up to date, {len(failed)} failed -> {out}")
    for trace_id, problems in failed:
        print(f"  FAIL {trace_id}")
        for p in problems:
            print(f"       {p}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
