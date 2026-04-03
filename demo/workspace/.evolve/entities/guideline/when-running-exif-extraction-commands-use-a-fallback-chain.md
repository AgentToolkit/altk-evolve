---
type: guideline
trigger: When writing portable EXIF extraction commands that must work across macOS environments
---

When running EXIF extraction commands, use a fallback chain: `exiftool file || exif file || mdls file` to handle environments where third-party tools may or may not be installed.

## Rationale

Different environments have different tools available. A || chain ensures the best available tool is used without requiring upfront knowledge of what is installed.
