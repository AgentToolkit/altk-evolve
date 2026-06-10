---
id: a0f68b14ae96
type: guideline
trigger: When counting occurrences of a literal token in a text file
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__d204b47c-4afa-4311-a957-5bc74caaa9a5.json
related_summary: summaries/d204b47c-4afa-4311-a957-5bc74caaa9a5.md
verified_at: 2026-06-10
---

# Count log lines matching a token via `grep -c '<token>' <path>`

`grep -c '<token>' <path>` returns just the count, no lines. Use `-i` for case-insensitive, `-E` for regex, `-w` for whole-word. To count lines matching ANY of several tokens: `grep -cE 'ERROR|FATAL|CRITICAL'`. Avoid `grep '<token>' | wc -l` — `-c` is shorter and works correctly with empty inputs.

## Rationale

`-c` is the right tool for the count-only case. `wc -l` adds an extra process and breaks when grep matches nothing.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/d204b47c-4afa-4311-a957-5bc74caaa9a5.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__d204b47c-4afa-4311-a957-5bc74caaa9a5.json)
