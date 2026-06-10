---
id: f0785632775e
type: guideline
trigger: When AGENTS.md tells you to consult the wiki, but the user's task may be outside its scope
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__6c2a7f4f-6e68-4515-9807-86ed692ca0a3.json
related_summary: summaries/6c2a7f4f-6e68-4515-9807-86ed692ca0a3.md
verified_at: 2026-06-10
---

# Validate wiki applicability via _index.jsonl before forcing a citation

After reading AGENTS.md, read `_index.jsonl` end-to-end and check whether any row's tags or trigger text overlaps your task's topical tags. If nothing overlaps, the wiki has nothing to offer — proceed with the task using your own approach. Don't force-fit an unrelated guideline into the response just because the pointer told you to consult.

## Rationale

The wiki recipe's value is sometimes negative — confirming inapplicability cheaply (≤2 reads) lets the agent proceed without forced citation.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/6c2a7f4f-6e68-4515-9807-86ed692ca0a3.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__6c2a7f4f-6e68-4515-9807-86ed692ca0a3.json)
