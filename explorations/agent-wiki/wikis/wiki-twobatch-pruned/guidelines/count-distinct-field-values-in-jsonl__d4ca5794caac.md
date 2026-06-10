---
id: d4ca5794caac
type: guideline
trigger: When summarizing a JSONL field's distinct values and `jq` is available
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__a14b6e18-4ac4-4fa5-83d0-df703ce86d47.json
related_summary: summaries/a14b6e18-4ac4-4fa5-83d0-df703ce86d47.md
verified_at: 2026-06-10
---

# Count distinct field values in JSONL via `jq -r '.field' | sort -u | wc -l`

`jq -r '.<field>' <path>` extracts one value per line. Pipe through `sort -u` for deduplication and `wc -l` for the count. If `jq` is missing: `python3 -c "import json; print(len({json.loads(l)['<field>'] for l in open('<path>')}))"`.

## Rationale

JSONL is line-delimited, per-line streaming works without loading the whole file. `jq` is the standard CLI; the Python fallback handles environments without `jq`.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/a14b6e18-4ac4-4fa5-83d0-df703ce86d47.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__a14b6e18-4ac4-4fa5-83d0-df703ce86d47.json)
