#!/usr/bin/env python3
"""Subscribe to another user's public guidelines repo.

- Adds entry to subscriptions list in evolve.config.yaml
- Clones the remote into .evolve/entities/subscribed/{name}
- Appends to audit.log
"""

import argparse
import os
import subprocess
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
from audit import append as audit_append  # noqa: E402

_GIT_TIMEOUT = 30  # seconds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Short subscription name (e.g. alice)")
    parser.add_argument("--remote", required=True, help="Git remote URL")
    parser.add_argument("--branch", default="main", help="Branch to track (default: main)")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    project_root = str(evolve_dir.resolve().parent)

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
    cloned_now = False
    if dest.exists():
        print(
            f"Error: directory already exists: {dest}\nRun evolve-lite:unsubscribe to remove it before re-subscribing.",
            file=sys.stderr,
        )
        sys.exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
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
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        cloned_now = True
    except subprocess.TimeoutExpired:
        print(f"Error: git clone timed out after {_GIT_TIMEOUT} seconds", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Error: git clone failed: {e.stderr}", file=sys.stderr)
        sys.exit(1)

    # Update config and audit - rollback clone on failure
    try:
        subscriptions.append({"name": args.name, "remote": args.remote, "branch": args.branch})
        cfg["subscriptions"] = subscriptions
        save_config(cfg, project_root)

        # Read identity.user for audit
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
        except Exception as e:
            print(f"Warning: audit log failed: {e}", file=sys.stderr)
    except Exception:
        # Rollback: remove cloned directory if config save failed
        if cloned_now and dest.exists():
            import shutil

            shutil.rmtree(dest)
        raise

    print(f"Subscribed to '{args.name}' from {args.remote}")


if __name__ == "__main__":
    main()

# Made with Bob
