---
id: 056000ede00b
type: guideline
trigger: Aggregating or parsing JSONL or other line-delimited record files whose exact schema you have not yet confirmed.
agent: bob
tags: [json, data-transformation, jsonl]
sources:
  - trajectories/df2b08e4-openai-chat-completions.analysis.json
related_summary: summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md
---

# Peek one JSONL line before writing the pipeline

Before writing any aggregation or parsing pipeline over JSONL (or other line-delimited records), read a few lines first with `head -n 3 <file>` and inspect the actual keys and value types. Confirm field names (`user`, `amount`, `items`, `tags`), whether arrays can be empty, and which fields are optional — then code the aggregation against the real shape. Do not infer the schema from the prompt's example block alone.

Guard for the variability you see: use `record.get("tags", [])` for fields that may be absent or empty rather than indexing directly.

## Rationale

A prompt's illustrative JSON often omits edge cases (empty arrays, optional keys, suffixed identifiers) that appear in the real data. Sampling a few lines reveals the true schema cheaply, so the first version of the pipeline parses every record correctly instead of crashing on a `KeyError` or miscounting after the files are already large and slow to reprocess.

## Sources

- [trajectory summary](../summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md)
- [normalized JSON](trajectories/df2b08e4-openai-chat-completions.analysis.json)
