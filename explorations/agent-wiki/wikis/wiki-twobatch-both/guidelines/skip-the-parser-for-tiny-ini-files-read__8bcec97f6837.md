---
id: 8bcec97f6837
type: guideline
trigger: When the INI file is small (<50 lines) and you need one specific key
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__4c526ddf-ce1a-41d0-9068-40eaeddf8f21.json
related_summary: summaries/4c526ddf-ce1a-41d0-9068-40eaeddf8f21.md
verified_at: 2026-06-10
---

# Skip the parser for tiny INI files — Read the file directly

Just Read the file. INI's syntax is human-readable; a one-shot value lookup doesn't need `configparser`. For larger files, or when you need iteration / case-insensitive sections / interpolation, switch to `configparser.ConfigParser()`.

## Rationale

Spinning up `configparser` is more code than reading the file when you only need one literal value. The trade-off flips around ~50 lines or for programmatic enumeration.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/4c526ddf-ce1a-41d0-9068-40eaeddf8f21.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__4c526ddf-ce1a-41d0-9068-40eaeddf8f21.json)
