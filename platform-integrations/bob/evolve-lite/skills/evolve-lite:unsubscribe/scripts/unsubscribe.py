#!/usr/bin/env python3
"""Remove a repo from the unified ``repos`` list and delete its local clone (Bob).

Usage:
  --list          Print repos as a JSON array and exit.
  --name {name}   Remove named repo from config and delete its local clone.
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

from audit import append as audit_append  # noqa: E402
from config import (  # noqa: E402
    is_valid_repo_name,
    load_config,
    normalize_repos,
    save_config,
    set_repos,
)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Print repos as JSON array")
    group.add_argument("--name", help="Name of repo to remove")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    project_root = str(evolve_dir.resolve()) if evolve_dir.name != ".evolve" else str(evolve_dir.resolve().parent)

    cfg = load_config(project_root)
    repos = normalize_repos(cfg)

    if args.list:
        print(json.dumps(repos, indent=2))
        return

    name = args.name
    subscribed_base = (evolve_dir / "entities" / "subscribed").resolve()
    dest = (evolve_dir / "entities" / "subscribed" / name).resolve()

    if not is_valid_repo_name(name) or dest == subscribed_base or not dest.is_relative_to(subscribed_base):
        print(f"Error: invalid subscription name: {name!r}", file=sys.stderr)
        sys.exit(1)

    new_repos = [r for r in repos if r.get("name") != name]
    if len(new_repos) == len(repos):
        print(f"Error: subscription '{name}' not found.", file=sys.stderr)
        sys.exit(1)

    if dest.exists():
        shutil.rmtree(dest)
        print(f"Deleted {dest}")
    else:
        print(f"Warning: {dest} did not exist.", file=sys.stderr)

    set_repos(cfg, new_repos)
    save_config(cfg, project_root)

    identity = cfg.get("identity", {})
    actor = identity.get("user", "unknown") if isinstance(identity, dict) else "unknown"
    audit_append(project_root=project_root, action="unsubscribe", actor=actor, name=name)

    print(f"Removed subscription '{name}' from config.")


if __name__ == "__main__":
    main()

# Made with Bob
