---
id: skill:extract-jpeg-exif-camera-optics
type: skill
name: extract-jpeg-exif-camera-optics
description: Read camera-optics fields (LensModel, FocalLength, ISO, Aperture) from a JPEG via stdlib `struct` when system EXIF tools are unavailable.
trigger: see SKILL.md
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__cd008bd4-19ca-4d40-9be7-395a96649c8d.json
related_summary: summaries/cd008bd4-19ca-4d40-9be7-395a96649c8d.md
verified_at: 2026-06-09
tags: [exif, jpeg, stdlib, struct, camera-optics]
---

# Extract Jpeg Exif Camera Optics

## Overview

Read camera-optics fields (LensModel, FocalLength, ISO, Aperture) from a JPEG via stdlib `struct` when system EXIF tools are unavailable.

## When To Use

- see SKILL.md

## Workflow

1. Run `bash <wiki>/skills/extract-jpeg-exif-camera-optics/scripts/run.sh ...`

## Sources

- [trajectory summary](../../summaries/cd008bd4-19ca-4d40-9be7-395a96649c8d.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__cd008bd4-19ca-4d40-9be7-395a96649c8d.json)
