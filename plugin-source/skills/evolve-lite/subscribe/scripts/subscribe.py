#!/usr/bin/env python3
"""Add a repo to the unified ``repos`` list and clone it locally.

Shared (multi-reader, multi-writer) repos are described in
``evolve.config.yaml``:

    repos:
      - name: memory
        scope: write
        remote: git@github.com:alice/evolve.git
        branch: main
        notes: public memory for foobar project
      - name: org-memory
        scope: read
        remote: git@github.com:acme/org-memory.git
        branch: main
        notes: private memory shared only within my org

``scope: read``  — download-only (pulled by sync).
``scope: write`` — publish target; also pulled by sync so you see what
                   others push and what you have already published.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Walk up from the script location to find the installed plugin lib directory.
# Every host installs the shared lib under lib/evolve-lite/ so multiple
# plugins can coexist side by side (e.g. .bob/lib/evolve-lite/).
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    _candidate = _ancestor / "lib" / "evolve-lite"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
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
    try:
        subprocess.run(clone_cmd, check=True, timeout=60, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        print("Error: git clone timed out", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        detail = (exc.stderr or exc.stdout or "").strip() or f"exit {exc.returncode}"
        print(f"Error: git clone failed: {detail}", file=sys.stderr)
        sys.exit(1)

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
        except Exception as save_exc:
            print(
                f"Warning: rollback save_config failed under {project_root!r}: {save_exc}. "
                f"The clone was removed but evolve.config.yaml may still list '{args.name}' - "
                f"please inspect the file and remove the entry manually if present.",
                file=sys.stderr,
            )
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        print(f"Error: failed to record subscription in audit log: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Subscribed to '{args.name}' (scope={args.scope}) from {args.remote}")


if __name__ == "__main__":
    main()
