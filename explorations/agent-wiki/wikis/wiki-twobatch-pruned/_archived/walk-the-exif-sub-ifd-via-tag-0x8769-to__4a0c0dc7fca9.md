---
id: 4a0c0dc7fca9
type: guideline
trigger: When extracting LensModel / FocalLength / aperture / ISO from a JPEG and Pillow / piexif / exiftool may be missing
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__cd008bd4-19ca-4d40-9be7-395a96649c8d.json
related_summary: summaries/cd008bd4-19ca-4d40-9be7-395a96649c8d.md
verified_at: 2026-06-09
---

# Walk the Exif sub-IFD via tag 0x8769 to read camera-optics fields

JPEG EXIF lives behind APP1 marker `0xFFE1`. Inside, after the literal `Exif\x00\x00`, sits the TIFF block. Walk IFD0 to find tag `0x8769` whose value is the offset of the Exif sub-IFD. Re-enter the IFD parser at that offset. Camera-optics fields like LensModel (`0xA434`), FocalLength (`0x920A`), Aperture (`0x829D`) live in this sub-IFD, not IFD0. Use stdlib `struct` and `<HHIB` (or `>HHIB` if big-endian) to unpack 12-byte IFD entries.

## Rationale

Scripts that only walk IFD0 miss every camera-optics tag — IFD0 carries Make/Model/Orientation/DateTime only. The 0x8769 indirection is the difference between the right answer and 'no such field'.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/cd008bd4-19ca-4d40-9be7-395a96649c8d.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__cd008bd4-19ca-4d40-9be7-395a96649c8d.json)
