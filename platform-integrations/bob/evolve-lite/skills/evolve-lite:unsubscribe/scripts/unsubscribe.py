#!/usr/bin/env python3
"""Remove a subscription and delete the locally cloned directory.

Usage:
  --list          Print subscriptions as a JSON array and exit.
  --name {name}   Remove named subscription from config and delete local dir.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Smart import: walk up to find evolve-lib
current = Path(__file__).resolve()
for parent in current.parents:
    lib_path = parent / "evolve-lib"
    if lib_path.exists():
        sys.path.insert(0, str(lib_path))
        break

from config import load_config, save_config  # noqa: E402
from audit import append as audit_append  # noqa: E402 # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Print subscriptions as JSON array")
    group.add_argument("--name", help="Name of subscription to remove")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve")).resolve()
    # Derive project_root from evolve_dir to ensure consistency
    project_root = str(evolve_dir.parent)

    cfg = load_config(project_root)
    subscriptions = cfg.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        subscriptions = []

    if args.list:
        print(json.dumps(subscriptions, indent=2))
        return

    # --name: remove the named subscription
    name = args.name

    # Validate name: resolve and confirm it stays within the subscribed directory
    subscribed_base = (evolve_dir / "entities" / "subscribed").resolve()
    dest = (evolve_dir / "entities" / "subscribed" / name).resolve()
    if not dest.is_relative_to(subscribed_base) or dest == subscribed_base:
        print(f"Error: invalid subscription name: {name!r}", file=sys.stderr)
        sys.exit(1)

    new_subs = [s for s in subscriptions if not (isinstance(s, dict) and s.get("name") == name)]

    if len(new_subs) == len(subscriptions):
        print(f"Error: subscription '{name}' not found.", file=sys.stderr)
        sys.exit(1)

    # Delete local clone
    if dest.exists():
        shutil.rmtree(dest)
        print(f"Deleted {dest}")
    else:
        print(f"Warning: {dest} did not exist.", file=sys.stderr)

    # Update config
    cfg["subscriptions"] = new_subs
    save_config(cfg, project_root)

    # Audit
    identity = cfg.get("identity", {})
    actor = identity.get("user", "unknown") if isinstance(identity, dict) else "unknown"
    audit_append(
        project_root=project_root,
        action="unsubscribe",
        actor=actor,
        name=name,
    )

    print(f"Removed subscription '{name}' from config.")


if __name__ == "__main__":
    main()

# Made with Bob
