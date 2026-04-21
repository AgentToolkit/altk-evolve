#!/usr/bin/env python3
"""Remove a subscription and delete the locally cloned directory."""

import argparse
import json
import os
import shutil
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Print subscriptions as JSON array")
    group.add_argument("--name", help="Name of subscription to remove")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    project_root = str(evolve_dir.resolve()) if evolve_dir.name != ".evolve" else str(evolve_dir.resolve().parent)

    cfg = load_config(project_root)
    subscriptions = cfg.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        subscriptions = []

    if args.list:
        print(json.dumps(subscriptions, indent=2))
        return

    name = args.name
    subscribed_base = (evolve_dir / "entities" / "subscribed").resolve()
    dest = (evolve_dir / "entities" / "subscribed" / name).resolve()
    if name in {"", "."} or dest == subscribed_base or not dest.is_relative_to(subscribed_base):
        print(f"Error: invalid subscription name: {name!r}", file=sys.stderr)
        sys.exit(1)

    legacy_base = (evolve_dir / "subscribed").resolve()
    legacy_dest = (evolve_dir / "subscribed" / name).resolve()
    if legacy_dest == legacy_base or not legacy_dest.is_relative_to(legacy_base):
        print(f"Error: invalid subscription name: {name!r}", file=sys.stderr)
        sys.exit(1)

    new_subs = [s for s in subscriptions if not (isinstance(s, dict) and s.get("name") == name)]
    if len(new_subs) == len(subscriptions):
        print(f"Error: subscription '{name}' not found.", file=sys.stderr)
        sys.exit(1)

    if dest.exists():
        shutil.rmtree(dest)
        print(f"Deleted {dest}")
    else:
        print(f"Warning: {dest} did not exist.", file=sys.stderr)

    if legacy_dest.exists():
        shutil.rmtree(legacy_dest)
        print(f"Deleted {legacy_dest}")

    cfg["subscriptions"] = new_subs
    save_config(cfg, project_root)

    identity = cfg.get("identity", {})
    actor = identity.get("user", "unknown") if isinstance(identity, dict) else "unknown"
    audit_append(project_root=project_root, action="unsubscribe", actor=actor, name=name)

    print(f"Removed subscription '{name}' from config.")


if __name__ == "__main__":
    main()
