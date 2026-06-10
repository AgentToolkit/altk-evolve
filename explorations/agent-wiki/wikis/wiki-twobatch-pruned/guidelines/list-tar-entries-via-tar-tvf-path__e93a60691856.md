---
id: e93a60691856
type: guideline
trigger: When you need TAR entry names + metadata and a unix `tar` is available
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__d6fff729-25ad-49eb-a1eb-8123eb33fb7d.json
related_summary: summaries/d6fff729-25ad-49eb-a1eb-8123eb33fb7d.md
verified_at: 2026-06-10
---

# List TAR entries via `tar -tvf <path>`

`tar -tvf <path>` lists entries one per line with mode, owner, size, mtime, name — strictly richer than `tarfile.getnames()`. Python fallback: `tarfile.open(path).getnames()` (names only) or `.getmembers()` (full metadata).

## Rationale

The unix `tar` is universal on macOS / Linux. Shelling out is shorter than Python and gives you size/permissions for free.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/d6fff729-25ad-49eb-a1eb-8123eb33fb7d.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__d6fff729-25ad-49eb-a1eb-8123eb33fb7d.json)
