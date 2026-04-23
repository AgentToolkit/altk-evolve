#!/usr/bin/env python3
"""Publish a private guideline entity to the public directory.

- Moves source from .evolve/entities/guideline/{filename}
- to .evolve/public/guideline/{filename}
- Updates frontmatter: visibility=public, owner={user}, published_at={now}
- Appends to audit.log
"""

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

# Smart import: walk up to find evolve-lib
current = Path(__file__).resolve()
for parent in current.parents:
    lib_path = parent / "evolve-lib"
    if lib_path.exists():
        sys.path.insert(0, str(lib_path))
        break

from entity_io import markdown_to_entity, entity_to_markdown  # noqa: E402
from audit import append as audit_append  # noqa: E402 # noqa: E402
from config import load_config  # noqa: E402 # noqa: E402


def _resolve_source(cfg, user_arg):
    """Derive a source label from config or fallback to user arg."""
    remote = cfg.get("public_repo", {})
    if isinstance(remote, dict):
        remote = remote.get("remote", "")
    if remote:
        # Extract user/repo from SSH or HTTPS remote URLs
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", remote)
        if m:
            return m.group(1)
    identity = cfg.get("identity", {})
    if isinstance(identity, dict) and identity.get("user"):
        return identity["user"]
    return user_arg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entity", required=True, help="Basename of the .md file to publish")
    parser.add_argument("--user", default=None, help="Username to stamp as owner")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))

    # Validate entity name is a simple basename (no path separators or special components)
    from pathlib import PurePath

    if PurePath(args.entity).name != args.entity or args.entity in (".", ".."):
        print(f"Error: invalid entity name: {args.entity!r}", file=sys.stderr)
        sys.exit(1)

    # Check for symlinks before resolving
    original_path = evolve_dir / "entities" / "guideline" / args.entity
    if original_path.is_symlink():
        print(f"Error: cannot publish symlinked entity: {args.entity}", file=sys.stderr)
        sys.exit(1)

    # Validate entity name: resolve and confirm it stays within the intended dir
    src_base = (evolve_dir / "entities" / "guideline").resolve()
    src_path = original_path.resolve()
    if not src_path.is_relative_to(src_base):
        print(f"Error: invalid entity name: {args.entity!r}", file=sys.stderr)
        sys.exit(1)

    if not src_path.exists():
        print(f"Error: entity file not found: {src_path}", file=sys.stderr)
        sys.exit(1)

    # Parse entity
    entity = markdown_to_entity(src_path)

    # Update frontmatter fields
    entity["visibility"] = "public"
    entity["published_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = load_config(str(evolve_dir.resolve().parent))

    # Determine user: prefer args.user, fallback to cfg.identity.user
    user = args.user
    if not user:
        identity = cfg.get("identity", {})
        if isinstance(identity, dict):
            user = identity.get("user")

    if user:
        entity["owner"] = user

    source = _resolve_source(cfg, args.user)
    if source:
        entity["source"] = source

    # Write to public directory
    dest_dir = evolve_dir / "public" / "guideline"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_base = dest_dir.resolve()
    dest_path = (dest_dir / args.entity).resolve()
    if not dest_path.is_relative_to(dest_base):
        print(f"Error: invalid entity name: {args.entity!r}", file=sys.stderr)
        sys.exit(1)

    if dest_path.exists():
        print(f"Error: already published: {dest_path}\nUnpublish it first or delete it manually.", file=sys.stderr)
        sys.exit(1)

    # Write to temp file in destination directory first, then atomic move
    content = entity_to_markdown(entity)
    temp_path = dest_path.with_suffix(".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(dest_path)
    original_path.unlink()

    # Audit log (don't let audit failures break the operation)
    try:
        audit_append(
            project_root=str(evolve_dir.resolve().parent),
            action="publish",
            actor=user or "unknown",
            entity=args.entity,
        )
    except Exception as e:
        print(f"Warning: failed to write audit log: {e}", file=sys.stderr)

    print(f"Published: {args.entity} -> {dest_path}")


if __name__ == "__main__":
    main()

# Made with Bob
