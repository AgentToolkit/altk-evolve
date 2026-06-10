---
id: e91cf5e787b3
type: guideline
trigger: When you need WAV header fields and Python is available
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__ad328a24-1c53-495e-ade1-4e3ae74e8302.json
related_summary: summaries/ad328a24-1c53-495e-ade1-4e3ae74e8302.md
verified_at: 2026-06-10
---

# Read WAV sample rate / channels / bit depth via stdlib `wave`

`wave.open(path, 'rb')` returns a reader with `.getframerate()`, `.getnchannels()`, `.getsampwidth()`, `.getnframes()`. Stdlib parses the RIFF container and `fmt ` subchunk. Use a `with` statement. Note: `wave` only handles uncompressed PCM.

## Rationale

Direct RIFF chunk navigation requires reading the `fmt ` subchunk's offset/length/field layout. Stdlib `wave` is shorter and handles the standard PCM layout.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/ad328a24-1c53-495e-ade1-4e3ae74e8302.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__ad328a24-1c53-495e-ade1-4e3ae74e8302.json)
