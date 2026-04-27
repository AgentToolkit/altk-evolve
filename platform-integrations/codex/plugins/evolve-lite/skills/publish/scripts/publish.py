#!/usr/bin/env python3
"""Publish a private guideline entity to a write-scope repo (Codex)."""

import argparse
import datetime
import os
import re
import sys
import tempfile
from pathlib import Path, PurePath

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
from entity_io import entity_to_markdown, markdown_to_entity  # noqa: E402
from config import get_repo, load_config, normalize_repos, write_repos  # noqa: E402


def _resolve_source(repo, effective_user):
    remote = repo.get("remote") if isinstance(repo, dict) else None
    if isinstance(remote, str):
        match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", remote)
        if match:
            return match.group(1)
    return effective_user


def _select_target_repo(cfg, requested_name):
    write = write_repos(cfg)

    if requested_name:
        repo = get_repo(cfg, requested_name)
        if repo is None:
            available = ", ".join(r["name"] for r in normalize_repos(cfg)) or "(none)"
            return None, f"no repo named '{requested_name}' is configured. Configured repos: {available}"
        if repo.get("scope") != "write":
            return None, f"repo '{requested_name}' has scope={repo.get('scope')!r}; publish requires scope=write"
        return repo, None

    if not write:
        return None, ("no write-scope repo configured. Run evolve-lite:subscribe with --scope write to set up a publish target.")
    if len(write) > 1:
        names = ", ".join(r["name"] for r in write)
        return None, f"multiple write-scope repos configured; pick one with --repo. Available: {names}"
    return write[0], None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entity", required=True, help="Basename of the .md file to publish")
    parser.add_argument("--user", default=None, help="Username to stamp as owner")
    parser.add_argument("--repo", default=None, help="Write-scope repo name (optional if exactly one is configured)")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    resolved_evolve_dir = evolve_dir.resolve()
    project_root = str(resolved_evolve_dir) if evolve_dir.name != ".evolve" else str(resolved_evolve_dir.parent)

    if PurePath(args.entity).name != args.entity or args.entity in {".", ".."}:
        print(f"Error: invalid entity name: {args.entity!r}", file=sys.stderr)
        sys.exit(1)

    src_base = (evolve_dir / "entities" / "guideline").resolve()
    src_path = (evolve_dir / "entities" / "guideline" / args.entity).resolve()

    if not src_path.is_relative_to(src_base):
        print(f"Error: invalid entity name: {args.entity!r}", file=sys.stderr)
        sys.exit(1)

    if not src_path.is_file():
        print(f"Error: entity file not found or is a directory: {src_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config(project_root)
    target, err = _select_target_repo(config, args.repo)
    if err is not None:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    identity = config.get("identity", {})
    effective_user = args.user or (identity.get("user") if isinstance(identity, dict) else None)

    entity = markdown_to_entity(src_path)
    entity["visibility"] = "public"
    if effective_user:
        entity["owner"] = effective_user
    entity["published_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    source = _resolve_source(target, effective_user)
    if source:
        entity["source"] = source

    clone_root = evolve_dir / "entities" / "subscribed" / target["name"]
    if not (clone_root / ".git").exists():
        print(
            f"Error: target repo clone not found at {clone_root}. "
            f"Run evolve-lite:subscribe with --scope write first, or evolve-lite:sync "
            f"to clone it.",
            file=sys.stderr,
        )
        sys.exit(1)
    dest_dir = clone_root / "guideline"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_base = dest_dir.resolve()
    dest_path = (dest_dir / args.entity).resolve()
    if not dest_path.is_relative_to(dest_base):
        print(f"Error: invalid entity name: {args.entity!r}", file=sys.stderr)
        sys.exit(1)
    if dest_path.exists():
        print(f"Error: already published: {dest_path}\nUnpublish it first or delete it manually.", file=sys.stderr)
        sys.exit(1)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=dest_dir,
            prefix=f".{args.entity}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(entity_to_markdown(entity))
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)

        temp_path.replace(dest_path)
        src_path.unlink()
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    try:
        audit_append(
            project_root=project_root,
            action="publish",
            actor=effective_user or "unknown",
            entity=args.entity,
            repo=target["name"],
        )
    except Exception as exc:
        print(f"Warning: failed to append audit entry for publish: {exc}", file=sys.stderr)

    print(f"Published: {args.entity} -> {dest_path} (repo: {target['name']})")


if __name__ == "__main__":
    main()
