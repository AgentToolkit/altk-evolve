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
    parser.add_argument("--type", type=str, required=True, help="Entity type (e.g., guideline)")
    parser.add_argument("--task", type=str, required=True, help="Task description (ignored in filesystem mode)")
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
        with open(entities_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading entities file: {e}", file=sys.stderr)
        sys.exit(1)

    all_entities = data.get("entities", [])

    # 3. Filter by type and sort (newest first)
    filtered = [e for e in all_entities if e.get("type") == args.type]

    # Sort by created_at descending (safely handle missing keys)
    filtered.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    # Apply limit
    results = filtered[:args.limit]

    if not results:
        print(f"No entities of type '{args.type}' found.")
        sys.exit(0)

    # 4. Format compact output to save LLM context tokens
    print(f"--- KAIZEN {args.type.upper()}S ({len(results)} found) ---\n")

    compact_results = []
    for e in results:
        metadata = e.get("metadata", {})
        compact_results.append({
            "content": e.get("content", ""),
            "rationale": metadata.get("rationale", ""),
            "trigger": metadata.get("trigger", ""),
            "category": metadata.get("category", "unknown")
        })

    print(json.dumps(compact_results, indent=2))
    print("\n--- END GUIDELINES ---")
    sys.exit(0)

if __name__ == "__main__":
    main()
