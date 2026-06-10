---
id: 4be167482caa
type: guideline
trigger: A task's deliverable is a file with a precise required structure, and a script has just written it.
agent: bob
tags: [verification, containers, data-transformation]
sources:
  - trajectories/df2b08e4-openai-chat-completions.analysis.json
related_summary: summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md
verified_at: 2026-06-10
cluster: let-the-system-tell-you-dont-assume__cluster.md
superseded_by: let-the-system-tell-you-dont-assume__cluster.md
---

# Verify the written output by reading it back

After a script reports success and writes a result file, read the file back (`docker exec <container> cat /app/aggregates.json`) and confirm it matches the required structure and types before declaring the task done. Check that nesting, key names, rounding, and integer-vs-float types match the spec exactly — a clean exit code and a 'done' print only prove the script ran, not that it produced the required shape.

## Rationale

A script can exit 0 and still emit output that violates the target schema (wrong nesting, unrounded floats, string counts instead of ints). Reading the actual file is the only check that the deliverable meets the spec, and it costs one cheap command compared to a silently-wrong submission.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md)
- [normalized JSON](trajectories/df2b08e4-openai-chat-completions.analysis.json)
