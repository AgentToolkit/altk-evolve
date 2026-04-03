---
type: guideline
trigger: When extracting image metadata (EXIF, GPS, camera info) on a macOS system
---

On macOS, use `mdls` as the primary tool for extracting image EXIF/metadata — it is always available as a native Spotlight utility and returns structured key-value pairs including GPS, camera model, timestamps, and more.

## Rationale

Tools like exiftool and exif are third-party and not installed by default on macOS. mdls is a built-in macOS command that reads Spotlight metadata and reliably surfaces EXIF fields without any installation.
