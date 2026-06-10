---
id: df9160ecdaf0
type: guideline
trigger: When the task is trivially answerable without external knowledge AND AGENTS.md's scope warning explicitly says don't read the wiki for trivial tasks
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__3d4ee0d1-d2fa-42a6-9457-14659dde7e89.json
related_summary: summaries/3d4ee0d1-d2fa-42a6-9457-14659dde7e89.md
verified_at: 2026-06-10
---

# Skip _index.jsonl when AGENTS.md scope warning rules out the task

AGENTS.md ships with: 'Don't read me for trivial tasks (typo fix, single-line refactor) or topics clearly outside the wiki's scope.' If the task is a single deterministic conversion, computation, or lookup that requires no external context, you can stop after reading AGENTS.md — skip _index.jsonl entirely. The wiki's recipe is opt-out for trivial cases.

## Rationale

Saves the index read when AGENTS.md alone is enough.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/3d4ee0d1-d2fa-42a6-9457-14659dde7e89.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__3d4ee0d1-d2fa-42a6-9457-14659dde7e89.json)
