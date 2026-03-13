#!/usr/bin/env python3
"""
Kaizen Skill: Recall (Stage 2 Filesystem Backend)
Reads entities from .kaizen/entities.json and outputs them in a compact format.
Zero dependencies (standard library only).
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Get Kaizen entities (Stage 2 Filesystem)")
    parser.add_argument("--type", type=str, default="guideline", help="Entity type (default: guideline)")
    parser.add_argument("--task", type=str, default="", help="Task description for relevance ranking")
    parser.add_argument("--limit", type=int, default=50, help="Max entities to return")
    args = parser.parse_args()

    # 1. Locate Storage
    workspace_root = Path.cwd()
    entities_file = workspace_root / ".kaizen" / "entities.json"

    if not entities_file.exists():
        print("No Kaizen guidelines exist yet. Complete some tasks to generate learnings!")
        sys.exit(0)

    # 2. Load Entities
    try:
        with open(entities_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading entities file: {e}", file=sys.stderr)
        sys.exit(1)

    all_entities = data.get("entities", [])

    # 3. Filter by type
    filtered = [ent for ent in all_entities if ent.get("type") == args.type]

    # Helper for basic text-matching relevance
    def _normalize(text):
        if not text:
            return set()
        import re

        words = re.findall(r"[a-z0-9]+", text.lower())
        return set(w for w in words if len(w) > 3)

    task_words = _normalize(args.task)

    def _get_relevance(entity):
        if not task_words:
            return 0
        content = entity.get("content", "")
        trigger = entity.get("metadata", {}).get("trigger", "")
        entity_words = _normalize(content + " " + trigger)
        if not entity_words:
            return 0
        return len(task_words & entity_words)

    # Sort by relevance (descending), then by created_at (descending)
    filtered.sort(key=lambda x: (_get_relevance(x), x.get("created_at", "")), reverse=True)

    # Apply limit
    results = filtered[: args.limit]

    if not results:
        print(f"No entities of type '{args.type}' found.")
        sys.exit(0)

    # 4. Format output as Markdown
    print(f"## KAIZEN {args.type.upper()}S ({len(results)} found)\n")
    print("Review these entities and apply any relevant ones to your current task:\n")

    for entity in results:
        content = entity.get("content", "")
        if not content:
            continue

        metadata = entity.get("metadata", {})
        category = metadata.get("category", "general")

        # Build the markdown bullet point
        item = f"- **[{category}]** {content}"

        # Add rationale and trigger if they exist
        rationale = metadata.get("rationale", "")
        trigger = metadata.get("trigger", "")

        if rationale:
            item += f"\n  - _Rationale: {rationale}_"
        if trigger:
            item += f"\n  - _When: {trigger}_"

        print(item)
        print()  # Empty line between entities

    print("\n--- END GUIDELINES ---")
    sys.exit(0)


if __name__ == "__main__":
    main()
