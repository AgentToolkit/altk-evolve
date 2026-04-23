#!/usr/bin/env python3
"""Pull the latest guidelines from all subscribed repos.

Subscribed repos are cloned directly into .evolve/entities/subscribed/{name}/
so the recall hook can read them without a separate mirror step.

Usage:
  --quiet        Suppress output if no changes.
  --config PATH  Path to config file (default: evolve.config.yaml at project root).
"""

import argparse
import os
import re
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

from config import load_config  # noqa: E402
from audit import append as audit_append  # noqa: E402


_GIT_TIMEOUT = 30  # seconds


def git_sync(repo_path, branch):
    """Fetch and hard-reset to origin. Returns CompletedProcess, or None on timeout.

    Hard reset ensures local clone always matches remote exactly — restores deleted
    files, discards any local modifications. Subscribed repos are read-only mirrors
    so there is nothing worth preserving locally.
    """
    try:
        fetch = subprocess.run(
            ["git", "-C", str(repo_path), "fetch", "origin", branch],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if fetch.returncode != 0:
            return fetch
        return subprocess.run(
            ["git", "-C", str(repo_path), "reset", "--hard", f"origin/{branch}"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"Warning: git sync timed out for {repo_path} (branch: {branch})", file=sys.stderr)
        return None


def count_delta(repo_path):
    """Count added/modified/deleted .md files since last pull.

    Returns dict: {added: int, updated: int, removed: int}
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "diff", "--name-status", "HEAD@{1}", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"Warning: git diff timed out for {repo_path} after {_GIT_TIMEOUT} seconds", file=sys.stderr)
        return {"added": 0, "updated": 0, "removed": 0}

    if result.returncode != 0:
        # HEAD@{1} doesn't exist (initial sync) — count all .md files as added
        added = len(list(repo_path.glob("**/*.md")))
        return {"added": added, "updated": 0, "removed": 0}
    added = updated = removed = 0
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        status, filename = parts[0].strip(), parts[1].strip()
        if not filename.endswith(".md"):
            continue
        if status.startswith("A"):
            added += 1
        elif status.startswith("M"):
            updated += 1
        elif status.startswith("D"):
            removed += 1
    return {"added": added, "updated": updated, "removed": removed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true", help="Suppress output if no changes")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config file (default: evolve.config.yaml in project root)",
    )
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))

    # Determine project_root from config path or EVOLVE_DIR
    if args.config:
        # Derive project_root from the directory containing the config file
        config_path = Path(args.config).resolve()
        project_root = str(config_path.parent)
    elif "EVOLVE_DIR" in os.environ:
        project_root = str(evolve_dir.parent)
    else:
        project_root = "."

    cfg = load_config(project_root)

    subscriptions = cfg.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        subscriptions = []

    if not subscriptions:
        if not args.quiet:
            print("No subscriptions configured.")
        sys.exit(0)

    identity = cfg.get("identity", {})
    actor = identity.get("user", "unknown") if isinstance(identity, dict) else "unknown"

    summaries = []
    total_delta = {}
    any_changes = False

    _SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
    subscribed_base = (evolve_dir / "entities" / "subscribed").resolve()

    for sub in subscriptions:
        if not isinstance(sub, dict):
            continue
        name = sub.get("name", "unknown")
        branch = sub.get("branch", "main")

        # Reject path traversal attempts and non-string names
        if not isinstance(name, str) or name in {".", ".."} or not _SAFE_NAME.match(name):
            summaries.append(f"{name!r} (skipped — invalid subscription name)")
            continue

        repo_path = evolve_dir / "entities" / "subscribed" / name

        # Defense-in-depth: verify resolved path is within subscribed directory
        repo_path_resolved = repo_path.resolve()
        if not repo_path_resolved.is_relative_to(subscribed_base) or repo_path_resolved == subscribed_base:
            summaries.append(f"{name!r} (skipped — path traversal detected)")
            continue

        if not repo_path.is_dir():
            summaries.append(f"{name} (not cloned — run evolve-lite:subscribe first)")
            continue

        pull_result = git_sync(repo_path, branch)
        if pull_result is None or pull_result.returncode != 0:
            summaries.append(f"{name} (sync failed — skipping)")
            total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
            continue

        delta = count_delta(repo_path)
        total_delta[name] = delta

        has_changes = any(v > 0 for v in delta.values())
        if has_changes:
            any_changes = True

        delta_str = f"+{delta['added']} added, {delta['updated']} updated, {delta['removed']} removed"
        summaries.append(f"{name} ({delta_str})")

    # Audit
    audit_append(
        project_root=project_root,
        action="sync",
        actor=actor,
        delta=total_delta,
    )

    if args.quiet and not any_changes:
        sys.exit(0)

    n = len(summaries)
    summary_line = f"Synced {n} repo(s): " + ", ".join(summaries)
    print(summary_line)


if __name__ == "__main__":
    main()

# Made with Bob
