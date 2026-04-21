#!/usr/bin/env python3
"""Pull the latest guidelines from all subscribed repos."""

import argparse
import os
import re
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
from config import _parse_yaml, load_config  # noqa: E402


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


def count_delta(repo_path):
    """Count added/modified/deleted .md files since last pull."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--name-status", "HEAD@{1}", "HEAD"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        # HEAD@{1} doesn't exist (initial sync) — count all .md files as added.
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
    parser.add_argument("--config", default=None, help="Explicit config path")
    parser.add_argument(
        "--session-start",
        action="store_true",
        help="Apply session-start gating for automatic hook execution",
    )
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    resolved_evolve_dir = evolve_dir.resolve()
    project_root = str(resolved_evolve_dir.parent)
    audit_root = resolved_evolve_dir if resolved_evolve_dir.name == ".evolve" else resolved_evolve_dir / ".evolve"

    if args.config:
        cfg_path = Path(args.config)
        cfg = _parse_yaml(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    else:
        cfg = load_config(project_root)

    sync_cfg = cfg.get("sync", {})
    if args.session_start and isinstance(sync_cfg, dict) and sync_cfg.get("on_session_start") is False:
        sys.exit(0)

    subscriptions = cfg.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        subscriptions = []

    if not subscriptions:
        if not args.quiet:
            print("No subscriptions configured. Add one with the evolve-lite:subscribe skill to start syncing shared guidelines.")
        sys.exit(0)

    identity = cfg.get("identity", {})
    actor = identity.get("user", "unknown") if isinstance(identity, dict) else "unknown"

    summaries = []
    total_delta = {}
    any_changes = False
    safe_name = re.compile(r"^[A-Za-z0-9._-]+$")

    for sub in subscriptions:
        if not isinstance(sub, dict):
            continue
        name = sub.get("name", "unknown")
        branch = sub.get("branch", "main")

        if not safe_name.match(name):
            summaries.append(f"{name!r} (skipped - invalid subscription name)")
            continue

        subscribed_base = (evolve_dir / "entities" / "subscribed").resolve()
        repo_path = (evolve_dir / "entities" / "subscribed" / name).resolve()
        legacy_base = (evolve_dir / "subscribed").resolve()
        legacy_repo_path = (evolve_dir / "subscribed" / name).resolve()

        if repo_path == subscribed_base or not repo_path.is_relative_to(subscribed_base):
            summaries.append(f"{name!r} (skipped - invalid subscription name)")
            continue

        if legacy_repo_path != legacy_base and legacy_repo_path.is_relative_to(legacy_base):
            if legacy_repo_path.exists() and not repo_path.exists():
                repo_path.parent.mkdir(parents=True, exist_ok=True)
                legacy_repo_path.rename(repo_path)
            elif legacy_repo_path.exists() and repo_path.exists():
                summaries.append(f"{name} (duplicate subscription folders — remove .evolve/subscribed/{name})")
                continue

        if not repo_path.is_dir():
            summaries.append(f"{name} (not cloned)")
            continue

        pull_result = git_pull(repo_path, branch)
        if pull_result is None or pull_result.returncode != 0:
            error_lines = (pull_result.stderr or pull_result.stdout or "").strip().splitlines()
            short_error = error_lines[-1] if error_lines else "unknown error"
            summaries.append(f"{name} (git pull failed: {short_error})")
            total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
            continue

        if "Already up to date" in (pull_result.stdout or ""):
            delta = {"added": 0, "updated": 0, "removed": 0}
        else:
            delta = count_delta(repo_path)
        total_delta[name] = delta
        if any(value > 0 for value in delta.values()):
            any_changes = True

        summaries.append(f"{name} (+{delta['added']} added, {delta['updated']} updated, {delta['removed']} removed)")

    audit_append(project_root=str(audit_root.parent), action="sync", actor=actor, delta=total_delta)

    if args.quiet and not any_changes:
        sys.exit(0)

    print(f"Synced {len(summaries)} repo(s): " + ", ".join(summaries))


if __name__ == "__main__":
    main()
