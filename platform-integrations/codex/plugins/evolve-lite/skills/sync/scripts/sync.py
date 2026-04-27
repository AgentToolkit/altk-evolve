#!/usr/bin/env python3
"""Pull the latest guidelines from every configured repo (Codex)."""

import argparse
import os
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
from config import is_valid_repo_name, load_config, normalize_repos  # noqa: E402


_GIT_TIMEOUT = 30  # seconds


def _git(repo_path, *args, timeout=_GIT_TIMEOUT):
    try:
        return subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None


def sync_read_only(repo_path, branch):
    """Fetch and hard-reset to origin/{branch} (read-only mirror)."""
    fetch = _git(repo_path, "fetch", "origin", branch)
    if fetch is None or fetch.returncode != 0:
        return fetch
    return _git(repo_path, "reset", "--hard", f"origin/{branch}")


def sync_writable(repo_path, branch):
    """Fetch and rebase local commits onto origin/{branch} (preserves publishes)."""
    fetch = _git(repo_path, "fetch", "origin", branch)
    if fetch is None or fetch.returncode != 0:
        return fetch
    rebase = _git(repo_path, "rebase", f"origin/{branch}")
    if rebase is None or rebase.returncode != 0:
        _git(repo_path, "rebase", "--abort")
        return rebase
    return rebase


def count_delta(repo_path):
    result = _git(repo_path, "diff", "--name-status", "HEAD@{1}", "HEAD")
    if result is None or result.returncode != 0:
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
        cfg = load_config(filepath=args.config)
    else:
        cfg = load_config(project_root)

    sync_cfg = cfg.get("sync", {})
    if args.session_start and isinstance(sync_cfg, dict) and sync_cfg.get("on_session_start") is False:
        sys.exit(0)

    repos = normalize_repos(cfg)

    if not repos:
        if not args.quiet:
            print("No subscriptions configured. Add one with the evolve-lite:subscribe skill to start syncing shared guidelines.")
        sys.exit(0)

    identity = cfg.get("identity", {})
    actor = identity.get("user", "unknown") if isinstance(identity, dict) else "unknown"

    summaries = []
    total_delta = {}
    any_changes = False

    for repo in repos:
        raw_name = repo.get("name", "unknown")
        scope = repo.get("scope", "read")
        branch = repo.get("branch", "main")
        remote = repo.get("remote")

        if not is_valid_repo_name(raw_name):
            summaries.append(f"{raw_name!r} (skipped - invalid subscription name)")
            continue
        name = raw_name.strip()

        if not isinstance(branch, str) or not branch.strip():
            summaries.append(f"{raw_name!r} (skipped - invalid subscription config)")
            continue
        branch = branch.strip()

        subscribed_base = (evolve_dir / "entities" / "subscribed").resolve()
        repo_path = (evolve_dir / "entities" / "subscribed" / name).resolve()

        if repo_path == subscribed_base or not repo_path.is_relative_to(subscribed_base):
            summaries.append(f"{name!r} (skipped - invalid subscription name)")
            continue

        if not repo_path.is_dir():
            if not remote:
                summaries.append(f"{name} (not cloned)")
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
                summaries.append(f"{name} (re-clone failed - timeout)")
                total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
                any_changes = True
                continue
            if clone_result.returncode != 0:
                summaries.append(f"{name} (re-clone failed: {clone_result.stderr.strip()})")
                total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
                any_changes = True
                continue

        if scope == "write":
            pull_result = sync_writable(repo_path, branch)
        else:
            pull_result = sync_read_only(repo_path, branch)

        if pull_result is None:
            summaries.append(f"{name} (sync failed - timeout)")
            total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
            any_changes = True
            continue
        if pull_result.returncode != 0:
            error_lines = (pull_result.stderr or pull_result.stdout or "").strip().splitlines()
            short_error = error_lines[-1] if error_lines else f"git exited with {pull_result.returncode}"
            summaries.append(f"{name} (sync failed: {short_error})")
            total_delta[name] = {"added": 0, "updated": 0, "removed": 0}
            any_changes = True
            continue

        delta = count_delta(repo_path)
        total_delta[name] = delta
        if any(value > 0 for value in delta.values()):
            any_changes = True

        summaries.append(f"{name} [{scope}] (+{delta['added']} added, {delta['updated']} updated, {delta['removed']} removed)")

    audit_append(project_root=str(audit_root.parent), action="sync", actor=actor, delta=total_delta)

    if args.quiet and not any_changes:
        sys.exit(0)

    print(f"Synced {len(summaries)} repo(s): " + ", ".join(summaries))


if __name__ == "__main__":
    main()
