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

# Add lib to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from config import load_config, save_config
from audit import append as audit_append


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Print subscriptions as JSON array")
    group.add_argument("--name", help="Name of subscription to remove")
    args = parser.parse_args()

    project_root = "."
    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))

    cfg = load_config(project_root)
    subscriptions = cfg.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        subscriptions = []

    if args.list:
        print(json.dumps(subscriptions, indent=2))
        return

    # --name: remove the named subscription
    name = args.name

    # Validate name: resolve both deletion targets and confirm they stay within
    # their intended directories before any filesystem operations
    subscribed_base = (evolve_dir / "subscribed").resolve()
    dest = (evolve_dir / "subscribed" / name).resolve()
    if not dest.is_relative_to(subscribed_base):
        print(f"Error: invalid subscription name: {name!r}", file=sys.stderr)
        sys.exit(1)

    entities_base = (evolve_dir / "entities" / "subscribed").resolve()
    entities_dest = (evolve_dir / "entities" / "subscribed" / name).resolve()
    if not entities_dest.is_relative_to(entities_base):
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

    # Delete mirrored entities so they stop appearing in recall
    if entities_dest.exists():
        shutil.rmtree(entities_dest)
        print(f"Deleted {entities_dest}")

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
