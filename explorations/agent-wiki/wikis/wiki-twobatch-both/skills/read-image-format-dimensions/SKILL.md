---
id: skill:read-image-format-dimensions
type: skill
name: read-image-format-dimensions
description: Read width/height (and version/bit-depth) from PNG, GIF, BMP, or WebP via stdlib `struct` + magic-byte dispatch when image libraries (Pillow, etc.) are unavailable.
trigger: When you need dimensions/version/bit-depth from a binary image format and Pillow / imagemagick may be missing
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__01984e90-b2c4-434a-8f87-bdb654ae10f6.json
related_summary: summaries/01984e90-b2c4-434a-8f87-bdb654ae10f6.md
verified_at: 2026-06-08
tags: [parsing, binary, image-format, headers, stdlib, struct]
---

# Read image-format dimensions

## Overview

One callable script that reads dimensions from PNG, GIF, BMP, or WebP by validating the format-specific magic bytes and unpacking fixed-position header fields with stdlib `struct`. No external image library required.

## When To Use

- Pillow / imagemagick / `identify` not installed
- Need dimensions (and for BMP, bit depth; for GIF, version string) of a binary image
- Have a path to one of: PNG / GIF87a / GIF89a / BMP / WebP (RIFF container)

## Workflow

1. Run `bash <wiki>/skills/read-image-format-dimensions/scripts/run.sh <image-path>`. The script auto-detects format from magic bytes (89 50 4E 47 = PNG, 'GIF87a'/'GIF89a' = GIF, 'BM' = BMP, 'RIFF...WEBP' = WebP).
2. It prints a single line with the dimensions (e.g. `100x100`) plus any format-specific extras (GIF version string; BMP bit depth).
3. If the script exits non-zero, the format wasn't recognized — fall back to inspecting the first 16 bytes manually.

## Sources

- [trajectory summary](../../summaries/01984e90-b2c4-434a-8f87-bdb654ae10f6.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__01984e90-b2c4-434a-8f87-bdb654ae10f6.json)
