#!/usr/bin/env python3
"""
Kaizen Skill: Recall (Stage 2 Filesystem Backend)
Reads entities from .kaizen/entities.json and outputs them in a compact format.
Zero dependencies (standard library only).
"""

import argparse
import json
import re
import sys
from pathlib import Path


def _normalize(text):
    """Lowercase and extract significant words (>3 chars) for overlap comparison."""
    words = re.findall(r'[a-z0-9]+', text.lower())
    return set(w for w in words if len(w) > 3)


def _relevance_score(task_words, entity):
    """Compute keyword overlap between task and entity content+trigger.
    Returns a float between 0.0 and 1.0."""
    if not task_words:
        return 0.0
    content = entity.get("content", "")
    trigger = entity.get("metadata", {}).get("trigger", entity.get("trigger", ""))
    entity_words = _normalize(content + " " + trigger)
    if not entity_words:
        return 0.0
    overlap = len(task_words & entity_words)
    return overlap / min(len(task_words), len(entity_words))


# Minimum relevance to include. Low bar — just filters out total mismatches.
RELEVANCE_THRESHOLD = 0.15


def main():
    parser = argparse.ArgumentParser(description="Get Kaizen entities (Stage 2 Filesystem)")
    parser.add_argument("--type", type=str, required=True, help="Entity type (e.g., guideline)")
    parser.add_argument("--task", type=str, required=True, help="Task description for relevance filtering")
    parser.add_argument("--limit", type=int, default=20, help="Max entities to return")
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

    # 3. Filter by type
    filtered = [e for e in all_entities if e.get("type") == args.type]

    if not filtered:
        print(f"No entities of type '{args.type}' found.")
        sys.exit(0)

    # 4. Score by relevance to task, filter out total mismatches, sort by score desc
    task_words = _normalize(args.task)

    scored = []
    for e in filtered:
        score = _relevance_score(task_words, e)
        scored.append((score, e))

    # Keep entities above threshold, sorted by relevance (highest first)
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [(s, e) for s, e in scored if s >= RELEVANCE_THRESHOLD]

    # Apply limit
    results = results[:args.limit]

    if not results:
        print(f"No relevant entities of type '{args.type}' found for task: \"{args.task}\"")
        sys.exit(0)

    # 5. Format compact output to save LLM context tokens
    total_type = len(filtered)
    print(f"--- KAIZEN {args.type.upper()}S ({len(results)} relevant of {total_type} total) ---\n")

    compact_results = []
    for score, e in results:
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
