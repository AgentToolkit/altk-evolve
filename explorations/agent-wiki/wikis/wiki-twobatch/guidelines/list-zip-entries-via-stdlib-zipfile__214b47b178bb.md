---
id: 214b47b178bb
type: guideline
trigger: When you need ZIP entry names and Python is available
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__e45c7a47-c30a-438d-9961-7ba3da638f6b.json
related_summary: summaries/e45c7a47-c30a-438d-9961-7ba3da638f6b.md
verified_at: 2026-06-10
---

# List ZIP entries via stdlib `zipfile.ZipFile().namelist()`

Use `zipfile.ZipFile(path).namelist()` — one call returns a list of strings. The stdlib reads the central directory; no struct manipulation needed. Use `infolist()` for sizes/dates/CRC32s alongside names.

## Rationale

ZIP central-directory parsing by hand is non-trivial (variable-length records, EOCD locator, optional zip64). Stdlib handles it.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/e45c7a47-c30a-438d-9961-7ba3da638f6b.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__e45c7a47-c30a-438d-9961-7ba3da638f6b.json)
