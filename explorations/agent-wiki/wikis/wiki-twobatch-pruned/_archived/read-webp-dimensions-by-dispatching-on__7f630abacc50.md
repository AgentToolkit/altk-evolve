---
id: 7f630abacc50
type: guideline
trigger: When you need WebP dimensions and the Pillow webp plugin may be missing
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__c41d3516-1299-46fc-9771-7ef044980ea8.json
related_summary: summaries/c41d3516-1299-46fc-9771-7ef044980ea8.md
verified_at: 2026-06-09
---

# Read WebP dimensions by dispatching on the RIFF subchunk type

WebP is a RIFF container. Validate bytes 0-3 = `RIFF` and 8-11 = `WEBP`. Read the 4-byte chunk type at offset 12 to dispatch: `VP8 ` (lossy — width/height at offset 26-29 as 14-bit LE pairs), `VP8L` (lossless — 14-bit (width-1) / (height-1) packed into 4 bytes after a 1-byte 0x2F signature), or `VP8X` (extended — 24-bit (width-1) / (height-1) starting at offset 24 of the VP8X chunk).

## Rationale

The three WebP variants encode dimensions differently. A naive read assuming one variant breaks on the others. Dispatch first.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/c41d3516-1299-46fc-9771-7ef044980ea8.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__c41d3516-1299-46fc-9771-7ef044980ea8.json)
