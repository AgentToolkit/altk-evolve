#!/usr/bin/env python3
"""Subscribe to another user's public guidelines repo.

- Adds entry to subscriptions list in evolve.config.yaml
- Clones the remote into .evolve/entities/subscribed/{name}
- Appends to audit.log
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from config import load_config, save_config
from audit import append as audit_append

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Short subscription name (e.g. alice)")
    parser.add_argument("--remote", required=True, help="Git remote URL")
    parser.add_argument("--branch", default="main", help="Branch to track (default: main)")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    project_root = str(evolve_dir.resolve().parent)

    if not _SAFE_NAME.match(args.name):
        print(
            f"Error: invalid subscription name: {args.name!r} (only A-Z, a-z, 0-9, '.', '_', '-' allowed)",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate name: resolve and confirm it stays within the subscribed directory
    subscribed_base = (evolve_dir / "entities" / "subscribed").resolve()
    dest = (evolve_dir / "entities" / "subscribed" / args.name).resolve()
    if not dest.is_relative_to(subscribed_base) or dest == subscribed_base:
        print(f"Error: invalid subscription name: {args.name!r}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(project_root)

    # Ensure subscriptions list exists
    subscriptions = cfg.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        subscriptions = []

    # Check for duplicate
    for sub in subscriptions:
        if isinstance(sub, dict) and sub.get("name") == args.name:
            print(
                f"Error: subscription '{args.name}' already exists in config.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Clone the repo
    if dest.exists():
        print(
            f"Error: directory already exists: {dest}\nRun /evolve-lite:unsubscribe to remove it before re-subscribing.",
            file=sys.stderr,
        )
        sys.exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "clone",
            args.remote,
            str(dest),
            "--branch",
            args.branch,
            "--depth",
            "1",
        ],
        check=True,
    )

    # Update config — roll back clone only if save_config fails
    subscriptions.append({"name": args.name, "remote": args.remote, "branch": args.branch})
    cfg["subscriptions"] = subscriptions
    try:
        save_config(cfg, project_root)
    except Exception as exc:
        subscriptions.pop()
        shutil.rmtree(dest, ignore_errors=True)
        print(f"Error: failed to record subscription — clone removed: {exc}", file=sys.stderr)
        sys.exit(1)

    # Audit — non-fatal; never deletes the clone or exits on failure
    identity = cfg.get("identity", {})
    actor = identity.get("user", "unknown") if isinstance(identity, dict) else "unknown"
    try:
        audit_append(
            project_root=project_root,
            action="subscribe",
            actor=actor,
            name=args.name,
            remote=args.remote,
        )
    except Exception as exc:
        print(f"Warning: audit log could not be updated: {exc}", file=sys.stderr)

    print(f"Subscribed to '{args.name}' from {args.remote}")


if __name__ == "__main__":
    main()
