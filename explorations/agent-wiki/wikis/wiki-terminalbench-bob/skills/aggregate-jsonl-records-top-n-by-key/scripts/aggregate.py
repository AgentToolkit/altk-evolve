#!/usr/bin/env python3
"""Aggregate JSONL records: top-N keys by summed amount + top-N tags by count.

Usage: python3 aggregate.py '<input-glob>' <output.json> [N]
Example: python3 aggregate.py '/app/records_*.jsonl' /app/aggregates.json 5

Edit GROUP_KEY / AMOUNT_FIELD / ITEMS_FIELD / TAGS_FIELD to match your schema.
"""
import json
import glob
import sys
from collections import defaultdict

GROUP_KEY = "user"
AMOUNT_FIELD = "amount"
ITEMS_FIELD = "items"
TAGS_FIELD = "tags"


def main():
    input_glob = sys.argv[1] if len(sys.argv) > 1 else "/app/records_*.jsonl"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "/app/aggregates.json"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 5

    group_data = defaultdict(lambda: {"total_amount": 0.0, "total_items": 0})
    tag_counts = defaultdict(int)

    for path in glob.glob(input_glob):
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                key = rec[GROUP_KEY]
                group_data[key]["total_amount"] += rec.get(AMOUNT_FIELD, 0)
                group_data[key]["total_items"] += rec.get(ITEMS_FIELD, 0)
                for tag in rec.get(TAGS_FIELD, []):
                    tag_counts[tag] += 1

    top_groups = dict(sorted(
        group_data.items(), key=lambda kv: kv[1]["total_amount"], reverse=True)[:n])
    for key in top_groups:
        top_groups[key]["total_amount"] = round(top_groups[key]["total_amount"], 2)
        top_groups[key]["total_items"] = int(top_groups[key]["total_items"])

    top_tags = dict(sorted(
        tag_counts.items(), key=lambda kv: kv[1], reverse=True)[:n])
    top_tags = {tag: {"count": int(c)} for tag, c in top_tags.items()}

    output = {
        "top_5_users_by_amount": top_groups,
        "top_5_tags_by_count": top_tags,
    }
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
