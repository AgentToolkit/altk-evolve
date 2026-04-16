#!/usr/bin/env python3
"""Subscribe to another user's public guidelines repo.

- Adds entry to subscriptions list in evolve.config.yaml
- Clones the remote into .evolve/subscribed/{name}
- Appends to audit.log
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from config import load_config, save_config
from audit import append as audit_append


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Short subscription name (e.g. alice)")
    parser.add_argument("--remote", required=True, help="Git remote URL")
    parser.add_argument("--branch", default="main", help="Branch to track (default: main)")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    project_root = str(evolve_dir.resolve().parent)

    # Validate name: resolve and confirm it stays within the subscribed directory
    subscribed_base = (evolve_dir / "subscribed").resolve()
    dest = (evolve_dir / "subscribed" / args.name).resolve()
    if not dest.is_relative_to(subscribed_base):
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
        print(f"Warning: {dest} already exists, skipping clone.", file=sys.stderr)
    else:
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

    # Update config
    subscriptions.append({"name": args.name, "remote": args.remote, "branch": args.branch})
    cfg["subscriptions"] = subscriptions
    save_config(cfg, project_root)

    # Read identity.user for audit
    identity = cfg.get("identity", {})
    actor = identity.get("user", "unknown") if isinstance(identity, dict) else "unknown"

    audit_append(
        project_root=project_root,
        action="subscribe",
        actor=actor,
        name=args.name,
        remote=args.remote,
    )

    print(f"Subscribed to '{args.name}' from {args.remote}")


if __name__ == "__main__":
    main()
