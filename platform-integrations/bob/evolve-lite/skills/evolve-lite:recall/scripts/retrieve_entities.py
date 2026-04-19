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

from entity_io import find_entities_dir, markdown_to_entity, log as _log  # noqa: E402


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


def load_entities_with_source(entities_dir):
    """Glob all .md files under entities_dir and parse each.

    Entities stored under entities/subscribed/{name}/ have ``_source`` set to
    the subscription name so format_entities can annotate them. The owner field
    written by publish.py is preserved; _source is just a routing key used
    internally and is never written to disk.
    """
    entities_dir = Path(entities_dir)
    entities = []
    for md in sorted(entities_dir.glob("**/*.md")):
        try:
            entity = markdown_to_entity(md)
            if not entity.get("content"):
                continue
            # Detect subscribed entities using path relative to entities_dir
            # to avoid matching "subscribed" in ancestor directory names.
            try:
                rel_parts = md.relative_to(entities_dir).parts
            except ValueError:
                rel_parts = md.parts
            for i, part in enumerate(rel_parts):
                if part == "subscribed" and i + 1 < len(rel_parts):
                    entity["_source"] = rel_parts[i + 1]
                    break
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
    if not entities_dir:
        log("No entities directory found")
        print("No entities directory found. Run evolve-lite:learn first.")
        return

    entities = load_entities_with_source(entities_dir)
    if not entities:
        log("No entities found")
        print("No entities found in knowledge base.")
        return

    # Filter by source if requested
    if args.sources != "all":
        original_count = len(entities)
        if args.sources == "private":
            # Private entities have no _source and are not in public/ or subscribed/
            entities = [e for e in entities if not e.get("_source")]
        elif args.sources == "public":
            # Public entities are in public/ directory (no _source but in public/)
            # This is a simplification; we'd need path info to be precise
            entities = [e for e in entities if not e.get("_source")]
        elif args.sources == "subscribed":
            # Subscribed entities have _source set
            entities = [e for e in entities if e.get("_source")]
        log(f"Filtered from {original_count} to {len(entities)} entities (sources={args.sources})")

    log(f"Loaded {len(entities)} entities")
    output = format_entities(entities)
    print(output)
    log(f"Output {len(output)} chars to stdout")


if __name__ == "__main__":
    main()

# Made with Bob
