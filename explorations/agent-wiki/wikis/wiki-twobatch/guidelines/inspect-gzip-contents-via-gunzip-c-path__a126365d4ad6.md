---
id: a126365d4ad6
type: guideline
trigger: When you need a peek at a gzipped file's content without fully decompressing
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__b4c7fc80-7e6e-4e26-8fc8-cd85685661f7.json
related_summary: summaries/b4c7fc80-7e6e-4e26-8fc8-cd85685661f7.md
verified_at: 2026-06-10
---

# Inspect gzip contents via `gunzip -c <path> | head`

`gunzip -c <path>` writes decompressed output to stdout (the `-c` keeps the original file). Pipe through `head -n N` for the first N lines. Python-only path: `gzip.open(path, 'rt').readline()`.

## Rationale

Streaming decompression via shell pipe is the lightweight path for log inspection / format sniffing.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/b4c7fc80-7e6e-4e26-8fc8-cd85685661f7.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__b4c7fc80-7e6e-4e26-8fc8-cd85685661f7.json)
