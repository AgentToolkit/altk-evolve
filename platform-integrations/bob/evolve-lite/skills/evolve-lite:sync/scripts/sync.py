#!/usr/bin/env python3
"""Pull the latest guidelines from all subscribed repos.

After pulling, copies .md files from .evolve/subscribed/{name}/ into
.evolve/entities/subscribed/{name}/ so the existing recall hook picks them
up without any changes.

Usage:
  --quiet        Suppress output if no changes.
  --config PATH  Path to config file (default: evolve.config.yaml at project root).
"""

import argparse
import os
import re
import shutil
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

from config import load_config, _parse_yaml  # noqa: E402
from audit import append as audit_append  # noqa: E402 # noqa: E402


_GIT_TIMEOUT = 30  # seconds


def git_pull(repo_path, branch):
    """Pull latest from origin. Returns CompletedProcess, or None on timeout."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_path), "pull", "origin", branch, "--ff-only"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"Warning: git pull timed out for {repo_path} (branch: {branch})", file=sys.stderr)
        return None


def copy_entities(subscribed_repo_path, entities_subscribed_path):
    """Mirror .md files from the subscribed git clone into entities/subscribed/{name}/.

    Clears the destination first so removed files don't linger.
    The owner field stamped at publish time travels with the file — no
    frontmatter manipulation needed here.
    """
    if entities_subscribed_path.exists():
        shutil.rmtree(entities_subscribed_path)
    for md in sorted(subscribed_repo_path.glob("**/*.md")):
        if md.is_symlink():
            continue
        rel = md.relative_to(subscribed_repo_path)
        dest = entities_subscribed_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(md, dest)


def count_delta(repo_path):
    """Count added/modified/deleted .md files since last pull.

    Returns dict: {added: int, updated: int, removed: int}
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--name-status", "HEAD@{1}", "HEAD"],
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

    project_root = "."
    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))

    # Determine config path
    if args.config:
        cfg_path = Path(args.config)
        # Load config from explicit path by temporarily reading the file
        if cfg_path.exists():
            cfg = _parse_yaml(cfg_path.read_text(encoding="utf-8"))
        else:
            cfg = {}
    else:
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

    for sub in subscriptions:
        if not isinstance(sub, dict):
            continue
        name = sub.get("name", "unknown")
        branch = sub.get("branch", "main")

        if not _SAFE_NAME.match(name):
            summaries.append(f"{name!r} (skipped — invalid subscription name)")
            continue

        repo_path = evolve_dir / "subscribed" / name

        if not repo_path.is_dir():
            summaries.append(f"{name} (not cloned — run evolve-lite:subscribe first)")
            continue

        pull_result = git_pull(repo_path, branch)
        if pull_result is None or pull_result.returncode != 0:
            summaries.append(f"{name} (pull failed — skipping mirror)")
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

        # Mirror entities into .evolve/entities/subscribed/{name}/
        entities_subscribed = evolve_dir / "entities" / "subscribed" / name
        copy_entities(repo_path, entities_subscribed)

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
