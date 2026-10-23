"""Run the pipelines without the UI: the same background worker the server runs, as a process of its own.

Usage:
  uv run altk-forge-worker                     # run every enabled pipeline until stopped (Ctrl+C / SIGTERM)
  uv run altk-forge-worker --once [--pipeline db2_ic]   # one poll of every (or one) enabled pipeline, then exit
  uv run altk-forge-worker --list              # pipelines and their counts

Configure integrations, filters and pipelines in the server's UI (or in .data/*.json), then run this on a server.
Do not run it next to `uv run altk-forge` on the same data folder: the server already runs the worker
(start the server with --no-worker to use both).
"""

import argparse
import signal
import sys
import time

from ..pipelines import startup
from ..pipelines.manage import list_pipelines
from ..pipelines.worker import WORKER


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="poll once and exit")
    ap.add_argument("--pipeline", help="only this pipeline (with --once)")
    ap.add_argument("--list", action="store_true", help="list pipelines and exit")
    args = ap.parse_args()
    startup()

    if args.list:
        for p in list_pipelines():
            c = p["counts"]
            print(
                f"{p['id']:<28} {p['mode']:<10} {'enabled' if p.get('enabled', True) else 'paused':<8} "
                f"{c['trajectories']} trajectories, {c['failed']} failed, {c['sent']} sent to memory"
            )
        return

    if args.once:
        ids = [args.pipeline] if args.pipeline else [p["id"] for p in list_pipelines() if p.get("enabled", True)]
        failed = False
        for pid in ids:
            run = WORKER.run_once(pid)
            failed |= run.get("status") == "failed"
            print(
                f"{pid}: {run.get('status')} · {run['found']} found, {run['built']} built, {run['failed']} failed, "
                f"{run['waiting']} settling, {run['sent']} sent" + (f" · {run['message']}" if run.get("message") else "")
            )
        sys.exit(1 if failed else 0)

    signal.signal(signal.SIGTERM, lambda *_: WORKER.stop())
    WORKER.start()
    print(f"Pipeline worker running ({len(list_pipelines())} pipelines). Ctrl+C to stop.", flush=True)
    try:
        while WORKER.running:
            time.sleep(1)
    except KeyboardInterrupt:
        WORKER.stop()


if __name__ == "__main__":
    main()
