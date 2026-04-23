#!/usr/bin/env python3
"""Subscribe to another user's public guidelines repo."""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Walk up from the script location to find the installed plugin lib directory.
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    for _candidate in (
        _ancestor / "lib",
        _ancestor / "platform-integrations" / "claude" / "plugins" / "evolve-lite" / "lib",
    ):
        if (_candidate / "entity_io.py").is_file():
            _lib = _candidate
            break
    if _lib is not None:
        break
if _lib is None:
    raise ImportError(f"Cannot find plugin lib directory above {_script}")
sys.path.insert(0, str(_lib))
from audit import append as audit_append  # noqa: E402
from config import load_config, save_config  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Short subscription name")
    parser.add_argument("--remote", required=True, help="Git remote URL")
    parser.add_argument("--branch", default="main", help="Branch to track")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    safe_name = re.compile(r"^[A-Za-z0-9._-]+$")
    project_root = str(evolve_dir.resolve()) if evolve_dir.name != ".evolve" else str(evolve_dir.resolve().parent)
    subscribed_base = (evolve_dir / "entities" / "subscribed").resolve()
    dest = (evolve_dir / "entities" / "subscribed" / args.name).resolve()
    legacy_dest = (evolve_dir / "subscribed" / args.name).resolve()
    if args.name in {"", "."} or not safe_name.match(args.name) or dest == subscribed_base or not dest.is_relative_to(subscribed_base):
        print(f"Error: invalid subscription name: {args.name!r}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(project_root)
    subscriptions = cfg.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        subscriptions = []

    for sub in subscriptions:
        if isinstance(sub, dict) and sub.get("name") == args.name:
            print(f"Error: subscription '{args.name}' already exists in config.", file=sys.stderr)
            sys.exit(1)

    if dest.exists() or legacy_dest.exists():
        print(f"Error: destination already exists: {dest}", file=sys.stderr)
        sys.exit(1)
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

    try:
        subscriptions.append({"name": args.name, "remote": args.remote, "branch": args.branch})
        cfg["subscriptions"] = subscriptions
        save_config(cfg, project_root)
    except Exception:
        if dest.exists():
            shutil.rmtree(dest)
        raise

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
        print(f"Warning: failed to append audit entry for subscribe: {exc}", file=sys.stderr)

    print(f"Subscribed to '{args.name}' from {args.remote}")


if __name__ == "__main__":
    main()
