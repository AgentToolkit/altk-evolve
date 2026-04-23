#!/usr/bin/env python3
"""Retrieve and output entities for Bob to filter."""

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
        if md.is_symlink():
            continue
        try:
            entity = markdown_to_entity(md)
            entity.pop("_source", None)
            if not entity.get("content"):
                continue
            try:
                rel_parts = md.relative_to(entities_dir).parts
            except ValueError:
                rel_parts = md.parts
            if rel_parts[0] == "subscribed" and len(rel_parts) > 1:
                entity["_source"] = rel_parts[1]
            entities.append(entity)
        except (OSError, UnicodeDecodeError):
            pass
    return entities


def main():
    log("Script started")

    entities_dir = find_entities_dir()
    log(f"Entities dir: {entities_dir}")

    entities = []
    if entities_dir:
        entities = load_entities_with_source(entities_dir)

    public_dir = get_evolve_dir() / "public"
    if public_dir.is_dir():
        log(f"Loading public entities from: {public_dir}")
        entities += load_entities_with_source(public_dir)

    if not entities:
        log("No entities found")
        return

    log(f"Loaded {len(entities)} entities")
    output = format_entities(entities)
    print(output)
    log(f"Output {len(output)} chars to stdout")


if __name__ == "__main__":
    main()

# Made with Bob
