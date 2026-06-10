---
id: f3a43cc1b801
type: guideline
trigger: Extracting the first element of a possibly-empty array as a field, where an empty array must produce JSON null.
agent: bob
tags: [jq, json, edge-cases]
sources:
  - trajectories/d0e03862-openai-chat-completions.analysis.json
related_summary: summaries/d0e03862-30c5-49b6-9aef-b97dcea57dc0.md
---

# Use the alternative operator for first-element defaults

When a derived field is the first element of an array that may be empty, use jq's `//` alternative operator to fall back to JSON null: `primary_role: (.roles[0] // null)`. This emits a real JSON `null` (not the string `"null"`) for empty arrays while still computing a sibling `role_count: (.roles | length)` correctly for any length.

## Rationale

`.roles[0]` on an empty array yields `null`, and `null // null` keeps it null, so the operator cleanly handles both populated and empty arrays without a conditional. Pairing it with `length` lets count and first-element extraction coexist in the same reshaped object. Specs that say 'null if empty' mean the JSON literal, which this produces.

## Sources

- [trajectory summary](../summaries/d0e03862-30c5-49b6-9aef-b97dcea57dc0.md)
- [normalized JSON](trajectories/d0e03862-openai-chat-completions.analysis.json)
