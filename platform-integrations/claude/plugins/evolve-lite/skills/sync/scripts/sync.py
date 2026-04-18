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

# Add lib to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from config import load_config
from audit import append as audit_append


_GIT_TIMEOUT = 30  # seconds


def git_sync(repo_path, branch):
    """Fetch and hard-reset to origin. Returns CompletedProcess, or None on timeout.

    Hard reset ensures local clone always matches remote exactly — restores deleted
    files, discards any local modifications. Subscribed repos are read-only mirrors
    so there is nothing worth preserving locally.
    """
    git_base = ["git", "-c", f"safe.directory={repo_path}", "-C", str(repo_path)]
    try:
        fetch = subprocess.run(
            [*git_base, "fetch", "origin", branch],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if fetch.returncode != 0:
            return fetch
        return subprocess.run(
            [*git_base, "reset", "--hard", f"origin/{branch}"],
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
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo_path}", "-C", str(repo_path), "diff", "--name-status", "HEAD@{1}", "HEAD"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
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
    project_root = str(evolve_dir.parent) if "EVOLVE_DIR" in os.environ else "."

    # Determine config path
    if args.config:
        cfg = load_config(filepath=args.config)
    else:
        cfg = load_config(project_root)

    # Check sync.on_session_start — only short-circuits automatic hook runs
    # (which pass --quiet). Manual invocations always execute.
    sync_cfg = cfg.get("sync", {})
    if args.quiet and isinstance(sync_cfg, dict) and sync_cfg.get("on_session_start") is False:
        sys.exit(0)

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

    for sub in subscriptions:
        if not isinstance(sub, dict):
            continue
        name = sub.get("name")
        branch = sub.get("branch", "main")

        if not isinstance(name, str) or not name.strip():
            summaries.append(f"{sub!r} (skipped — missing or non-string name)")
            continue
        name = name.strip()

        if not isinstance(branch, str) or not branch.strip():
            summaries.append(f"{name!r} (skipped — missing or non-string branch)")
            continue
        branch = branch.strip()

        if not _SAFE_NAME.match(name):
            summaries.append(f"{name!r} (skipped — invalid subscription name)")
            continue

        repo_path = evolve_dir / "entities" / "subscribed" / name

        if not repo_path.is_dir():
            remote = sub.get("remote")
            if not remote:
                summaries.append(f"{name} (not cloned — no remote in config, run /evolve-lite:subscribe first)")
                continue
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            clone_result = subprocess.run(
                ["git", "clone", remote, str(repo_path), "--branch", branch, "--depth", "1"],
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT,
            )
            if clone_result.returncode != 0:
                summaries.append(f"{name} (re-clone failed: {clone_result.stderr.strip()})")
                total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
                continue

        pull_result = git_sync(repo_path, branch)
        if pull_result is None or pull_result.returncode != 0:
            summaries.append(f"{name} (sync failed — skipping)")
            total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
            continue

        if "Already up to date" in (pull_result.stdout or ""):
            delta = {"added": 0, "updated": 0, "removed": 0}
        else:
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
