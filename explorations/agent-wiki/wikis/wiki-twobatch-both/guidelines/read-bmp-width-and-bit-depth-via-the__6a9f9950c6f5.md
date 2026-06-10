---
id: 6a9f9950c6f5
type: guideline
trigger: When you need BMP dimensions or bit depth from raw bytes
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-2__dd0259ee-4935-4d32-9728-55a9599c4945.json
related_summary: summaries/1b560d86-8c35-497a-907d-075eba07683b.md
verified_at: 2026-06-10
---

# Read BMP width and bit depth via the BITMAPINFOHEADER offsets

Validate the first 2 bytes are `BM` (the file header). The BITMAPINFOHEADER begins at byte 14. Width is at file offset 18 (uint32 LE, 4 bytes); bit depth (`biBitCount`) is at offset 28 (uint16 LE). `struct.unpack('<I', data[18:22])` and `struct.unpack('<H', data[28:30])`.

## Rationale

BMP has multiple DIB-header variants but BITMAPINFOHEADER is dominant. Width/bpp offsets are stable across all common variants.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/1b560d86-8c35-497a-907d-075eba07683b.md)
- [normalized JSON](trajectories/claude_md_strong__trial-2__dd0259ee-4935-4d32-9728-55a9599c4945.json)
