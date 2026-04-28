#!/usr/bin/env python3
"""Add a repo to the unified ``repos`` list and clone it locally (Codex)."""

import argparse
import os
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
from config import (  # noqa: E402
    VALID_SCOPES,
    is_valid_repo_name,
    load_config,
    normalize_repos,
    save_config,
    set_repos,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Short repo name")
    parser.add_argument("--remote", required=True, help="Git remote URL")
    parser.add_argument("--branch", default="main", help="Branch to track")
    parser.add_argument("--scope", default="read", choices=VALID_SCOPES)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    project_root = str(evolve_dir.resolve()) if evolve_dir.name != ".evolve" else str(evolve_dir.resolve().parent)
    subscribed_base = (evolve_dir / "entities" / "subscribed").resolve()
    dest = (evolve_dir / "entities" / "subscribed" / args.name).resolve()

    if not is_valid_repo_name(args.name) or dest == subscribed_base or not dest.is_relative_to(subscribed_base):
        print(f"Error: invalid subscription name: {args.name!r}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(project_root)
    repos = normalize_repos(cfg)

    for repo in repos:
        if repo.get("name") == args.name:
            print(f"Error: subscription '{args.name}' already exists in config.", file=sys.stderr)
            sys.exit(1)

    if dest.exists():
        print(f"Error: destination already exists: {dest}", file=sys.stderr)
        sys.exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write-scope repos need full history so the user can safely rebase and
    # push publish commits. Read-scope repos only mirror, so shallow is enough.
    clone_cmd = ["git", "clone", args.remote, str(dest), "--branch", args.branch]
    if args.scope == "read":
        clone_cmd += ["--depth", "1"]
    subprocess.run(clone_cmd, check=True)

    repos.append(
        {
            "name": args.name,
            "scope": args.scope,
            "remote": args.remote,
            "branch": args.branch,
            "notes": args.notes,
        }
    )
    set_repos(cfg, repos)
    try:
        save_config(cfg, project_root)
    except Exception:
        repos.pop()
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
            scope=args.scope,
            remote=args.remote,
        )
    except Exception as exc:
        repos.pop()
        set_repos(cfg, repos)
        try:
            save_config(cfg, project_root)
        except Exception:
            pass
        if dest.exists():
            shutil.rmtree(dest)
        print(f"Error: failed to record subscription — clone removed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Subscribed to '{args.name}' (scope={args.scope}) from {args.remote}")


if __name__ == "__main__":
    main()
