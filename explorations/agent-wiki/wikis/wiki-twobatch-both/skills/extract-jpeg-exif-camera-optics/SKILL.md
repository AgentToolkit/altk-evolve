---
id: skill:extract-jpeg-exif-camera-optics
type: skill
name: extract-jpeg-exif-camera-optics
description: Read camera-optics fields (LensModel, FocalLength, ISO, Aperture) from a JPEG via stdlib `struct` when system EXIF tools are unavailable.
trigger: When you need any non-GPS, non-IFD0 EXIF field from a JPEG and Pillow / piexif / exiftool may be missing
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__cd008bd4-19ca-4d40-9be7-395a96649c8d.json
related_summary: summaries/cd008bd4-19ca-4d40-9be7-395a96649c8d.md
verified_at: 2026-06-08
tags: [exif, jpeg, stdlib, struct, camera-optics]
---

# Extract JPEG EXIF camera-optics fields

## Overview

Read camera-optics EXIF fields (LensModel, FocalLength, Aperture, ISO) from a JPEG using stdlib `struct`. Validates the APP1 marker, walks IFD0 to the Exif sub-IFD via tag 0x8769, and extracts the requested tag.

## When To Use

- Pillow / piexif / exiftool not installed in the environment
- Need any of: LensModel (0xA434), FocalLength (0x920A), Aperture (0x829D), ISO (0x8827), LensMake (0xA433)
- Have a path to a JPEG and want the field as a string or number with one tool call

## Workflow

1. Identify which EXIF tag you need (LensModel = 0xA434, FocalLength = 0x920A, Aperture = 0x829D, ISO = 0x8827).
2. Run `bash <wiki>/skills/extract-jpeg-exif-camera-optics/scripts/run.sh <jpeg-path> <tag-hex>` (e.g. `0xA434`). The script handles APP1 location, validation, IFD walking, sub-IFD entry via 0x8769, and tag extraction.
3. Report the value to the user. If the script exits non-zero, the JPEG either has no Exif sub-IFD or the requested tag is absent — both are valid 'not found' answers.

## Sources

- [trajectory summary](../../summaries/cd008bd4-19ca-4d40-9be7-395a96649c8d.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__cd008bd4-19ca-4d40-9be7-395a96649c8d.json)
