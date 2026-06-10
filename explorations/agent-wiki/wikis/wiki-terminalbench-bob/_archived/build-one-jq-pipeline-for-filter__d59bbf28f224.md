---
id: d59bbf28f224
type: guideline
trigger: Transforming a JSON array with jq where the spec combines filtering, field extraction/renaming, computed fields, and a final sort.
agent: bob
tags: [jq, json, data-transformation]
sources:
  - trajectories/d0e03862-openai-chat-completions.analysis.json
related_summary: summaries/d0e03862-30c5-49b6-9aef-b97dcea57dc0.md
---

# Build one jq pipeline for filter-reshape-sort

When a task asks to filter, rename/reshape fields, derive computed values, and sort JSON records, express the whole transformation as a single jq pipeline rather than chaining multiple jq invocations or post-processing in another language. Wrap the per-record `select(...) | {reshaped object}` in an array constructor and end with `sort_by(.key)`, e.g. `[.[] | select(.status == "active") | {user_id: .id, ...}] | sort_by(.username)`. Inspect the input file with `cat` first to confirm the actual field names and shapes before writing the filter.

## Rationale

A single pipeline keeps the array context intact so `sort_by` operates on the collected results, produces output in one pass, and avoids intermediate files. Reshaping inside the array constructor guarantees every emitted record has the exact target schema. Reading the input first prevents guessing field names that don't exist.

## Sources

- [trajectory summary](../summaries/d0e03862-30c5-49b6-9aef-b97dcea57dc0.md)
- [normalized JSON](trajectories/d0e03862-openai-chat-completions.analysis.json)
