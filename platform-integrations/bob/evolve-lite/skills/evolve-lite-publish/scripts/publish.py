#!/usr/bin/env python3
"""Publish a private guideline entity to a write-scope repo."""

import argparse
import datetime
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePath

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
from entity_io import entity_to_markdown, markdown_to_entity, write_clone_gitignore  # noqa: E402
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


def _run_dedup(project_root):
    dedup_script = Path(project_root) / ".bob" / "skills" / "evolve-lite-dedup" / "scripts" / "dedup.py"
    if not dedup_script.is_file():
        print(f"Error: dedup script not found at {dedup_script}", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        [sys.executable, str(dedup_script), "--local-only"],
        cwd=project_root,
    )
    if result.returncode != 0:
        print("Error: dedup failed; publish aborted before push.", file=sys.stderr)
        sys.exit(result.returncode)


ENTITY_TYPES = ("guideline", "atomic-skill", "skill-flow")


def _find_entity(evolve_dir, entity_arg):
    """Locate the source entity file.

    Accepts:
      - bare filename: ``my-skill.md``  (searches all three type dirs)
      - type-prefixed:  ``atomic-skill/general/my-skill.md``
      - product-relative: ``general/my-skill.md``  (searches all type dirs)
    """
    p = PurePath(entity_arg)

    # Reject obvious traversal attempts
    if any(part in {".", ".."} for part in p.parts):
        return None, None, f"invalid entity name: {entity_arg!r}"

    # Case 1: first component is an explicit entity type
    if p.parts[0] in ENTITY_TYPES:
        entity_type = p.parts[0]
        rel_rest = Path(*p.parts[1:]) if len(p.parts) > 1 else Path(p.name)
        src_base = (evolve_dir / "entities" / entity_type).resolve()
        candidate = (src_base / rel_rest).resolve()
        if not candidate.is_relative_to(src_base):
            return None, None, f"invalid entity name: {entity_arg!r}"
        if candidate.is_file():
            return candidate, entity_type, None
        return None, None, f"entity file not found: {candidate}"

    # Case 2: bare name or product/name — search all type dirs
    filename = p.name
    matches = []
    for et in ENTITY_TYPES:
        src_base = (evolve_dir / "entities" / et).resolve()
        for found in src_base.glob(f"**/{filename}"):
            if found.is_file() and found.is_relative_to(src_base):
                matches.append((found, et))

    if not matches:
        return None, None, f"entity file not found: {entity_arg!r} (searched all type dirs)"
    if len(matches) > 1:
        paths = ", ".join(str(m) for m, _ in matches)
        return None, None, f"ambiguous entity name {entity_arg!r}; found in multiple locations: {paths}"
    return matches[0][0], matches[0][1], None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--entity",
        required=True,
        help="Entity to publish: bare filename, 'product/name.md', or 'type/product/name.md'",
    )
    parser.add_argument("--user", default=None, help="Username to stamp as owner")
    parser.add_argument("--repo", default=None, help="Write-scope repo name (optional if exactly one is configured)")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))
    resolved_evolve_dir = evolve_dir.resolve()
    project_root = str(resolved_evolve_dir) if evolve_dir.name != ".evolve" else str(resolved_evolve_dir.parent)

    src_path, entity_type, err = _find_entity(evolve_dir, args.entity)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    src_base = (evolve_dir / "entities" / entity_type).resolve()

    _run_dedup(project_root)

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

    # Version management: start at 1 on first publish; bump on re-publish.
    try:
        current_version = int(entity.get("version", "0") or "0")
    except (ValueError, TypeError):
        current_version = 0
    new_version = current_version + 1
    entity["version"] = str(new_version)

    clone_root = evolve_dir / "entities" / "subscribed" / target["name"]
    if not (clone_root / ".git").exists():
        print(
            f"Error: target repo clone not found at {clone_root}. "
            f"Run evolve-lite:subscribe with --scope write first, or evolve-lite:sync "
            f"to clone it.",
            file=sys.stderr,
        )
        sys.exit(1)

    expected_branch = target.get("branch", "main")
    expected_remote = target.get("remote", "").strip()
    try:
        branch_result = subprocess.run(
            ["git", "-C", str(clone_root), "symbolic-ref", "--short", "HEAD"],
            capture_output=True,
            text=True,
        )
        if branch_result.returncode != 0:
            print(
                f"Error: could not determine current branch of clone at {clone_root}: {branch_result.stderr.strip()}",
                file=sys.stderr,
            )
            sys.exit(1)
        current_branch = branch_result.stdout.strip()
        if current_branch != expected_branch:
            print(
                f"Error: clone at {clone_root} is on branch '{current_branch}', "
                f"expected '{expected_branch}' (from evolve.config.yaml). "
                f"Run: git -C \"{clone_root}\" checkout \"{expected_branch}\"",
                file=sys.stderr,
            )
            sys.exit(1)

        remote_result = subprocess.run(
            ["git", "-C", str(clone_root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        if remote_result.returncode != 0:
            print(
                f"Error: could not determine remote URL of clone at {clone_root}: {remote_result.stderr.strip()}",
                file=sys.stderr,
            )
            sys.exit(1)
        actual_remote = remote_result.stdout.strip()
        if actual_remote != expected_remote:
            print(
                f"Error: clone at {clone_root} has remote '{actual_remote}', "
                f"expected '{expected_remote}' (from evolve.config.yaml). "
                f"The clone does not match the configured write-scope repo.",
                file=sys.stderr,
            )
            sys.exit(1)
    except FileNotFoundError:
        print("Error: git not found on PATH", file=sys.stderr)
        sys.exit(1)

    # Ensure the clone has a protective .gitignore before writing anything.
    # Idempotent — only writes when the file is absent or ours to update.
    write_clone_gitignore(clone_root)

    relative_src_parent = src_path.parent.relative_to(src_base)
    dest_dir = clone_root / ".evolve" / "entities" / entity_type / relative_src_parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_base = dest_dir.resolve()
    filename = src_path.name
    dest_path = (dest_dir / filename).resolve()
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
            prefix=f".{filename}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(entity_to_markdown(entity))
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)

        temp_path.replace(dest_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    try:
        audit_append(
            project_root=project_root,
            action="publish",
            actor=effective_user or "unknown",
            entity=filename,
            repo=target["name"],
            version=new_version,
        )
    except Exception as exc:
        print(f"Warning: failed to append audit entry for publish: {exc}", file=sys.stderr)

    version_note = f"v{new_version}" if new_version > 1 else "v1 (first publish)"
    print(f"Published: {filename} -> {dest_path} (type: {entity_type}, repo: {target['name']}, {version_note})")
    if new_version > 1:
        print(f"  Tip: add a '## Changelog' section to document what changed in v{new_version}.")


if __name__ == "__main__":
    main()
