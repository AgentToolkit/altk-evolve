---
id: 599e2d3b582b
type: guideline
trigger: When parsing CSV and any field might contain a comma, newline, or embedded quote
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__ae7be18d-661a-4b5c-b87c-0b32f977ecb4.json
related_summary: summaries/ae7be18d-661a-4b5c-b87c-0b32f977ecb4.md
verified_at: 2026-06-09
---

# Use stdlib `csv.reader` with `newline=''` for CSVs that may have quoted commas

Open with `newline=''` (REQUIRED — without it, embedded newlines inside quoted fields break the row boundary). Then `csv.reader(f)` walks rows respecting RFC 4180 quoting. Naive `line.split(',')` is wrong whenever a field contains `","`.

## Rationale

RFC 4180 quoting is common in real CSVs. Always reach for `csv.reader` first; the `newline=''` argument is a sharp edge that bites every time it's omitted.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/ae7be18d-661a-4b5c-b87c-0b32f977ecb4.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__ae7be18d-661a-4b5c-b87c-0b32f977ecb4.json)
