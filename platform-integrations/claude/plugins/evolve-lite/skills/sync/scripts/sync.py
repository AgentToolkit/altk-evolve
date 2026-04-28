#!/usr/bin/env python3
"""Pull the latest guidelines from every configured repo.

Every repo in ``evolve.config.yaml`` (both read- and write-scope) is cloned
into ``.evolve/entities/subscribed/{name}/`` so recall sees everything through
a single root. Publish commits stay local until pushed, so write-scope repos
use ``git pull --rebase`` (preserves unpushed commits) while read-scope repos
use ``git fetch`` + ``git reset --hard`` (exact mirror).

Usage:
  --quiet            Suppress output if no changes.
  --config PATH      Path to config file (default: evolve.config.yaml at project root).
  --session-start    Apply the ``sync.on_session_start`` gate (automatic hook runs).
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from config import is_valid_repo_name, load_config, normalize_repos  # noqa: E402
from audit import append as audit_append  # noqa: E402


_GIT_TIMEOUT = 30  # seconds


def _git(repo_path, *args, timeout=_GIT_TIMEOUT):
    """Run a git command inside ``repo_path`` with a timeout. Returns CompletedProcess or None."""
    try:
        return subprocess.run(
            ["git", "-c", f"safe.directory={repo_path}", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None


def _head_hash(repo_path):
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo_path}", "-C", str(repo_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def sync_read_only(repo_path, branch):
    """Fetch and hard-reset to ``origin/{branch}``. Returns CompletedProcess or None on timeout.

    Hard reset ensures the local clone always matches the remote exactly —
    restores deleted files and discards any local modifications. Read-only
    mirrors have no local commits worth preserving.
    """
    fetch = _git(repo_path, "fetch", "origin", branch)
    if fetch is None or fetch.returncode != 0:
        return fetch
    return _git(repo_path, "reset", "--hard", f"origin/{branch}")


def sync_writable(repo_path, branch):
    """Fetch and rebase local commits onto ``origin/{branch}``.

    Write-scope repos may have local commits from publishing that have not
    yet been pushed. Rebase preserves them (no-op when the working tree is
    clean) so the user never loses unpushed publish commits.
    """
    fetch = _git(repo_path, "fetch", "origin", branch)
    if fetch is None or fetch.returncode != 0:
        return fetch
    rebase = _git(repo_path, "rebase", f"origin/{branch}")
    if rebase is None or rebase.returncode != 0:
        # Abort a failed rebase so we don't leave the repo in a conflict state.
        _git(repo_path, "rebase", "--abort")
        return rebase
    return rebase


def count_delta(repo_path):
    """Count added/modified/deleted .md files since last sync.

    Returns dict: ``{added: int, updated: int, removed: int}``.
    """
    result = _git(
        repo_path,
        "diff",
        "--name-status",
        "HEAD@{1}",
        "HEAD",
    )
    if result is None or result.returncode != 0:
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
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config file (default: evolve.config.yaml in project root)",
    )
    parser.add_argument(
        "--session-start",
        action="store_true",
        help="Apply session-start gating for automatic hook execution",
    )
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    project_root = str(evolve_dir.parent) if "EVOLVE_DIR" in os.environ else "."

    if args.config:
        cfg = load_config(filepath=args.config)
    else:
        cfg = load_config(project_root)

    # Check sync.on_session_start — only short-circuits automatic hook runs.
    sync_cfg = cfg.get("sync", {})
    if args.session_start and isinstance(sync_cfg, dict) and sync_cfg.get("on_session_start") is False:
        sys.exit(0)

    repos = normalize_repos(cfg)

    if not repos:
        if not args.quiet:
            print("No subscriptions configured. Add one with /evolve-lite:subscribe to start syncing shared guidelines.")
        sys.exit(0)

    identity = cfg.get("identity", {})
    actor = identity.get("user", "unknown") if isinstance(identity, dict) else "unknown"

    summaries = []
    total_delta = {}
    any_changes = False

    for repo in repos:
        name = repo.get("name")
        scope = repo.get("scope", "read")
        branch = repo.get("branch", "main")
        remote = repo.get("remote")

        if not is_valid_repo_name(name):
            summaries.append(f"{name!r} (skipped — invalid subscription name)")
            continue

        repo_path = evolve_dir / "entities" / "subscribed" / name

        head_before = None
        if not repo_path.is_dir():
            if not remote:
                summaries.append(f"{name} (not cloned — no remote in config, run /evolve-lite:subscribe first)")
                continue
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            clone_cmd = ["git", "clone", "--branch", branch]
            if scope == "read":
                clone_cmd += ["--depth", "1"]
            clone_cmd += ["--", remote, str(repo_path)]
            try:
                clone_result = subprocess.run(
                    clone_cmd,
                    capture_output=True,
                    text=True,
                    timeout=_GIT_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                summaries.append(f"{name} (re-clone failed — timeout)")
                total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
                any_changes = True
                continue
            if clone_result.returncode != 0:
                summaries.append(f"{name} (re-clone failed: {clone_result.stderr.strip()})")
                total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
                any_changes = True
                continue
        else:
            head_before = _head_hash(repo_path)

        if scope == "write":
            pull_result = sync_writable(repo_path, branch)
        else:
            pull_result = sync_read_only(repo_path, branch)

        if pull_result is None:
            summaries.append(f"{name} (sync failed — timeout)")
            total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
            any_changes = True
            continue
        if pull_result.returncode != 0:
            err = (pull_result.stderr or pull_result.stdout or "").strip().splitlines()
            short_error = err[-1] if err else f"git exited with {pull_result.returncode}"
            summaries.append(f"{name} (sync failed: {short_error})")
            total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
            any_changes = True
            continue

        head_after = _head_hash(repo_path)
        if head_before is not None and head_before == head_after:
            delta = {"added": 0, "updated": 0, "removed": 0}
        else:
            delta = count_delta(repo_path)
        total_delta[name] = delta

        has_changes = any(v > 0 for v in delta.values())
        if has_changes:
            any_changes = True

        delta_str = f"+{delta['added']} added, {delta['updated']} updated, {delta['removed']} removed"
        summaries.append(f"{name} [{scope}] ({delta_str})")

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
