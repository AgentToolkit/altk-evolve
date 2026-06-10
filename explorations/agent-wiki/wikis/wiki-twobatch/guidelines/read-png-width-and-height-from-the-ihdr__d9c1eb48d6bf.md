---
id: d9c1eb48d6bf
type: guideline
trigger: When you need PNG dimensions and Pillow / image tools may be unavailable
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__01984e90-b2c4-434a-8f87-bdb654ae10f6.json
related_summary: summaries/01984e90-b2c4-434a-8f87-bdb654ae10f6.md
verified_at: 2026-06-10
---

# Read PNG width and height from the IHDR chunk via stdlib struct

Validate the 8-byte signature `\x89PNG\r\n\x1a\n` first. The IHDR chunk follows immediately (4-byte length, 4-byte type 'IHDR'). Width and height are the first 8 bytes of the IHDR data — at file offsets 16 and 20, each a big-endian 4-byte unsigned int. Read 24 bytes; `struct.unpack('>II', data[16:24])`.

## Rationale

PNG's IHDR position is fixed by spec since 1996. Reading 24 bytes is sufficient; no need to decode IDAT.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/01984e90-b2c4-434a-8f87-bdb654ae10f6.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__01984e90-b2c4-434a-8f87-bdb654ae10f6.json)
