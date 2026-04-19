#!/usr/bin/env python3
"""Retrieve and output entities for Claude to filter."""

import json
import os
import sys
from pathlib import Path

# Add lib to path so we can import entity_io
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from entity_io import find_entities_dir, get_evolve_dir, markdown_to_entity, log as _log


def log(message):
    _log("retrieve", message)


log("Script started")

# Log all environment variables
log("=== Environment Variables ===")
for key, value in sorted(os.environ.items()):
    # Mask sensitive values
    if any(sensitive in key.upper() for sensitive in ["PASSWORD", "SECRET", "TOKEN", "KEY", "API"]):
        log(f"  {key}=***MASKED***")
    else:
        log(f"  {key}={value}")
log("=== End Environment Variables ===")

# Log command-line arguments
log("=== Command-Line Arguments ===")
log(f"  sys.argv: {sys.argv}")
log(f"  Script path: {sys.argv[0] if sys.argv else 'N/A'}")
log(f"  Arguments: {sys.argv[1:] if len(sys.argv) > 1 else 'None'}")
log("=== End Command-Line Arguments ===")


def format_entities(entities):
    """Format all entities for Claude to review.

    Entities that came from a subscribed source have their path recorded in
    the private ``_source`` key (set by load_entities_with_source). These are
    annotated with ``[from: {name}]`` so Claude knows their provenance.
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
        except (OSError, UnicodeError):
            pass
    return entities


def main():
    # Read input from stdin (hook provides JSON with prompt)
    try:
        input_data = json.load(sys.stdin)
        log("=== Input Data ===")
        log(f"  Keys: {list(input_data.keys())}")
        log(f"  Full content: {json.dumps(input_data, indent=2)}")
        log("=== End Input Data ===")
    except json.JSONDecodeError as e:
        log(f"Failed to parse JSON input: {e}")
        return

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
