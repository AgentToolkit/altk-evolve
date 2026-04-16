#!/usr/bin/env python3
"""Publish a private guideline entity to the public directory.

- Reads source from .evolve/entities/guideline/{filename}
- Copies to .evolve/public/guideline/{filename}
- Updates frontmatter: visibility=public, owner={user}, published_at={now}
- Appends to audit.log
"""
import argparse
import datetime
import os
import shutil
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from entity_io import markdown_to_entity, entity_to_markdown
from audit import append as audit_append


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entity", required=True, help="Basename of the .md file to publish")
    parser.add_argument("--user", default=None, help="Username to stamp as owner")
    args = parser.parse_args()

    evolve_dir = Path(os.environ.get("EVOLVE_DIR", ".evolve"))

    # Validate entity name: resolve and confirm it stays within the intended dir
    src_base = (evolve_dir / "entities" / "guideline").resolve()
    src_path = (evolve_dir / "entities" / "guideline" / args.entity).resolve()
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
    if args.user:
        entity["owner"] = args.user
    entity["published_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write to public directory
    dest_dir = evolve_dir / "public" / "guideline"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_base = dest_dir.resolve()
    dest_path = (dest_dir / args.entity).resolve()
    if not dest_path.is_relative_to(dest_base):
        print(f"Error: invalid entity name: {args.entity!r}", file=sys.stderr)
        sys.exit(1)

    content = entity_to_markdown(entity)
    dest_path.write_text(content, encoding="utf-8")

    # Audit log
    audit_append(
        project_root=str(evolve_dir.parent) if evolve_dir.name != ".evolve" else ".",
        action="publish",
        actor=args.user or "unknown",
        entity=args.entity,
    )

    print(f"Published: {args.entity} -> {dest_path}")


if __name__ == "__main__":
    main()
