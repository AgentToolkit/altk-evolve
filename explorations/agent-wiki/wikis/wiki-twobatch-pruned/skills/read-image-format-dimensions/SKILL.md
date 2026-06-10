---
id: skill:read-image-format-dimensions
type: skill
name: read-image-format-dimensions
description: Read width/height (and version/bit-depth) from PNG, GIF, BMP, or WebP via stdlib `struct` + magic-byte dispatch when image libraries (Pillow, etc.) are unavailable.
trigger: see SKILL.md
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__01984e90-b2c4-434a-8f87-bdb654ae10f6.json
related_summary: summaries/01984e90-b2c4-434a-8f87-bdb654ae10f6.md
verified_at: 2026-06-09
tags: [parsing, binary, image-format, headers, stdlib, struct]
---

# Read Image Format Dimensions

## Overview

Read width/height (and version/bit-depth) from PNG, GIF, BMP, or WebP via stdlib `struct` + magic-byte dispatch when image libraries (Pillow, etc.) are unavailable.

## When To Use

- see SKILL.md

## Workflow

1. Run `bash <wiki>/skills/read-image-format-dimensions/scripts/run.sh ...`

## Sources

- [trajectory summary](../../summaries/01984e90-b2c4-434a-8f87-bdb654ae10f6.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__01984e90-b2c4-434a-8f87-bdb654ae10f6.json)
