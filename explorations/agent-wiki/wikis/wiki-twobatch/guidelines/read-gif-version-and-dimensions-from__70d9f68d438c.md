---
id: 70d9f68d438c
type: guideline
trigger: When you need GIF version + dimensions without Pillow
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__5c0737e6-f786-4ef7-9e8b-bdbf3a6d3545.json
related_summary: summaries/5c0737e6-f786-4ef7-9e8b-bdbf3a6d3545.md
verified_at: 2026-06-10
---

# Read GIF version and dimensions from the first 10 bytes via stdlib struct

GIF header layout: bytes 0-5 are the signature ASCII (`GIF87a` or `GIF89a`). Bytes 6-7 are width (uint16 little-endian); bytes 8-9 are height (uint16 little-endian). Read 10 bytes; decode signature as ASCII and `struct.unpack('<HH', data[6:10])`.

## Rationale

GIF's logical-screen-descriptor is fixed-position. A 10-byte read is sufficient — no image-data decoding needed.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/5c0737e6-f786-4ef7-9e8b-bdbf3a6d3545.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__5c0737e6-f786-4ef7-9e8b-bdbf3a6d3545.json)
