#!/usr/bin/env python3
"""Retrieve and output entities for Bob to filter."""

import argparse
import sys
from pathlib import Path

# Smart import: walk up to find evolve-lib
current = Path(__file__).resolve()
for parent in current.parents:
    lib_path = parent / "evolve-lib"
    if lib_path.exists():
        sys.path.insert(0, str(lib_path))
        break

from entity_io import find_entities_dir, get_evolve_dir, markdown_to_entity, log as _log  # noqa: E402


def log(message):
    _log("retrieve", message)


def format_entities(entities):
    """Format all entities for Bob to review.

    Entities that came from a subscribed source have their path recorded in
    the private ``_source`` key (set by load_entities_with_source). These are
    annotated with ``[from: {name}]`` so Bob knows their provenance.
    """
    header = """## Entities for this task

Review these entities and apply any relevant ones:

"""
    items = []
    for e in entities:
        content = e.get("content")
        if not content:
            continue
        source = e.get("_source")
        if source:
            content = f"[from: {source}] {content}"
        item = f"- **[{e.get('type', 'general')}]** {content}"
        if e.get("rationale"):
            item += f"\n  - _Rationale: {e['rationale']}_"
        if e.get("trigger"):
            item += f"\n  - _When: {e['trigger']}_"
        items.append(item)

    return header + "\n".join(items)


def load_entities_with_source(entities_dir, kind="private"):
    """Glob all .md files under entities_dir and parse each.

    Entities stored under entities/subscribed/{name}/ have ``_source`` set to
    the subscription name so format_entities can annotate them. The owner field
    written by publish.py is preserved; _source is just a routing key used
    internally and is never written to disk.
    
    Args:
        entities_dir: Directory to load entities from
        kind: Source kind tag - "private", "public", or "subscribed"
    """
    entities_dir = Path(entities_dir)
    entities = []
    for md in sorted(entities_dir.glob("**/*.md")):
        try:
            entity = markdown_to_entity(md)
            if not entity.get("content"):
                continue
            # Clear any _source from frontmatter to ensure provenance is derived
            # exclusively from the file path structure
            entity.pop("_source", None)
            # Tag with source kind for filtering
            entity["_kind"] = kind
            # Detect subscribed entities using path relative to entities_dir
            # Only set _source when the FIRST path segment is "subscribed"
            try:
                rel_parts = md.relative_to(entities_dir).parts
            except ValueError:
                rel_parts = md.parts
            if len(rel_parts) > 1 and rel_parts[0] == "subscribed":
                entity["_source"] = rel_parts[1]
                entity["_kind"] = "subscribed"
            entities.append(entity)
        except OSError:
            pass
    return entities


def main():
    parser = argparse.ArgumentParser(description="Retrieve entities from knowledge base")
    parser.add_argument(
        "--sources",
        choices=["all", "private", "public", "subscribed"],
        default="all",
        help="Which entity sources to include (default: all)",
    )
    args = parser.parse_args()

    log(f"Script started with sources={args.sources}")

    entities_dir = find_entities_dir()
    log(f"Entities dir: {entities_dir}")

    # Load entities from all sources
    entities = []
    if entities_dir:
        entities = load_entities_with_source(entities_dir, kind="private")
        log(f"Loaded {len(entities)} private entities")

    public_dir = get_evolve_dir() / "public"
    if public_dir.is_dir():
        public_entities = load_entities_with_source(public_dir, kind="public")
        log(f"Loaded {len(public_entities)} public entities")
        entities += public_entities

    if not entities:
        log("No entities found in any source")
        print("No entities found in knowledge base.")
        return

    # Filter by source kind if requested
    if args.sources != "all":
        original_count = len(entities)
        entities = [e for e in entities if e.get("_kind") == args.sources]
        log(f"Filtered from {original_count} to {len(entities)} entities (sources={args.sources})")

    log(f"Loaded {len(entities)} entities")
    output = format_entities(entities)
    print(output)
    log(f"Output {len(output)} chars to stdout")


if __name__ == "__main__":
    main()

# Made with Bob
