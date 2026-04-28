#!/usr/bin/env python3
"""Remove a repo from the unified ``repos`` list and delete its local clone (Codex)."""

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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Required to remove a write-scope repo (its clone may hold unpushed publishes)",
    )
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

    matched = next((r for r in repos if r.get("name") == name), None)
    if matched is None:
        print(f"Error: subscription '{name}' not found.", file=sys.stderr)
        sys.exit(1)

    if matched.get("scope") == "write" and not args.force:
        print(
            f"Error: '{name}' is a write-scope repo. Removing it would delete the local clone, "
            "including any unpushed publish commits. Re-run with --force to confirm.",
            file=sys.stderr,
        )
        sys.exit(1)

    new_repos = [r for r in repos if r.get("name") != name]

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
