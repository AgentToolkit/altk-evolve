#!/usr/bin/env python3
"""
Kaizen Skill: Learn (Stage 2 Filesystem Backend)
Reads extracted entities from stdin and saves them to .kaizen/entities.json.
Zero dependencies (standard library only).
"""

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path


def main():
    print("[Kaizen Learn] Processing extracted entities...")

    # Detect if called with CLI arguments (common agent mistake)
    if len(sys.argv) > 1:
        print("ERROR: This script does not accept CLI arguments.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Correct usage (pipe JSON via stdin):", file=sys.stderr)
        print(
            '  printf \'{"entities": [{"content": "...", "rationale": "...", "category": "strategy", "trigger": "..."}]}\' | python3 <path-to-script>/save.py',
            file=sys.stderr,
        )
        sys.exit(1)

    input_data = sys.stdin.read().strip()
    if not input_data:
        print("Error: No data provided via stdin.", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(input_data)
        new_entities = data.get("entities", [])
        if not new_entities:
            print("No entities found in the input JSON.", file=sys.stderr)
            sys.exit(0)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON from stdin: {e}", file=sys.stderr)
        print(f"Input snippet: {input_data[:200]}...", file=sys.stderr)
        sys.exit(1)

    # 2. Setup Storage Directory
    workspace_root = Path.cwd()
    kaizen_dir = workspace_root / ".kaizen"
    entities_file = kaizen_dir / "entities.json"

    if not kaizen_dir.exists():
        print(f"Creating storage directory: {kaizen_dir}")
        kaizen_dir.mkdir(parents=True, exist_ok=True)

    # 3. Load Existing Entities
    existing_data = {"entities": []}
    if entities_file.exists():
        try:
            with open(entities_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception as e:
            print(f"Error: Could not read existing entities file: {e}", file=sys.stderr)
            sys.exit(1)

    existing_entities = existing_data.get("entities", [])

    # 4. Merge and Deduplicate
    added_count = 0
    from datetime import timezone

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Deduplication: exact match set + semantic similarity check
    seen = {(e.get("content", ""), e.get("trigger", e.get("metadata", {}).get("trigger", ""))) for e in existing_entities}

    def _normalize(text):
        """Lowercase and extract significant words (>3 chars) for overlap comparison."""
        import re

        words = re.findall(r"[a-z0-9]+", text.lower())
        return set(w for w in words if len(w) > 3)

    def _is_semantically_similar(new_content, new_trigger, existing_entities, threshold=0.6):
        """Check if a new entity is semantically similar to any existing one.
        Returns (True, matching_content) if similar, (False, None) otherwise."""
        new_words = _normalize(new_content + " " + new_trigger)
        if not new_words:
            return False, None
        for e in existing_entities:
            existing_content = e.get("content", "")
            existing_trigger = e.get("metadata", {}).get("trigger", e.get("trigger", ""))
            existing_words = _normalize(existing_content + " " + existing_trigger)
            if not existing_words:
                continue
            overlap = len(new_words & existing_words) / min(len(new_words), len(existing_words))
            if overlap >= threshold:
                return True, existing_content
        return False, None

    for entity in new_entities:
        content = entity.get("content", "")
        trigger = entity.get("trigger", "")

        # Skip exact duplicates
        if (content, trigger) in seen:
            continue

        # Skip semantically similar entities
        is_similar, match = _is_semantically_similar(content, trigger, existing_entities)
        if is_similar:
            print(f'  ~ Skipped (similar to existing): "{content[:60]}..."')
            print(f'    Existing: "{match[:60]}..."')
            continue

        # Format entity for storage
        storable_entity = {
            "id": str(uuid.uuid4()),
            "type": "guideline",
            "content": content,
            "metadata": {"category": entity.get("category", "strategy"), "trigger": trigger, "rationale": entity.get("rationale", "")},
            "created_at": now_iso,
        }

        existing_entities.append(storable_entity)
        seen.add((content, trigger))
        added_count += 1

        print(f"  + [{storable_entity['metadata']['category']}] {content[:80]}...")

    # 5. Save back to disk
    if added_count > 0:
        existing_data["entities"] = existing_entities
        try:
            import os
            import tempfile

            temp_fd, temp_path = tempfile.mkstemp(dir=entities_file.parent, prefix="entities_tmp_", suffix=".json")
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, entities_file)
            print(f"\n✅ Successfully saved {added_count} new entities to {entities_file}")
            print(f"📊 Total entities in memory: {len(existing_entities)}")
        except Exception as e:
            print(f"Error writing to entities file: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("\nℹ️ No new unique entities to add.")

    sys.exit(0)


if __name__ == "__main__":
    main()
