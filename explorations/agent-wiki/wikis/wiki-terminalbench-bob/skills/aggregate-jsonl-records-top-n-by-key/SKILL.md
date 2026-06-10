---
id: skill:aggregate-jsonl-records-top-n-by-key
type: skill
name: aggregate-jsonl-records-top-n-by-key
description: Stream-read many JSONL record files, sum a numeric field per group key and tally tag occurrences, then emit the top-N groups and top-N tags as a structured JSON file.
trigger: "A task hands you several large line-delimited (JSONL) record files and asks for aggregates: top-N entities ranked by a summed numeric field, plus top-N values counted across an array field, written to a JSON file with a precise shape."
agent: bob
sources:
  - trajectories/df2b08e4-openai-chat-completions.analysis.json
related_summary: summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md
verified_at: 2026-06-09
tags: [jsonl, aggregation, streaming]
---

# Aggregate Jsonl Records Top N By Key

## Overview

Aggregate a set of JSONL files line-by-line with the stdlib: accumulate per-key sums (and item counts) and per-tag counts in dictionaries, sort to take the top N of each, and write the result as JSON with the exact required nesting, rounding, and integer/float types.

## When To Use

- The input is multiple JSONL files (e.g. records_*.jsonl), each line a JSON object, often large enough that loading everything into memory or shelling out per-line is wasteful.
- The deliverable is a JSON file ranking the top-N keys by a summed numeric field and/or the top-N values by occurrence count across an array field.
- The required output structure specifies exact key names, rounding (e.g. amounts to 2 decimals), and integer-vs-float types that a one-liner would get wrong.

## Workflow

1. Inspect the real schema before coding: list the inputs and read a few lines of one file (e.g. `head -n 3 <file>`) to confirm the actual key names, value types, and which array fields can be empty. Do not trust the prompt's illustrative example alone.
2. Code the aggregation against the confirmed shape, using `dict.get(field, default)` for fields that may be absent or empty (e.g. an empty/missing tags array) so a single odd record cannot crash the run.
3. Run the aggregator over a glob of the input files, streaming one line at a time so memory stays flat regardless of file size. Adapt the field names, sort keys, N, rounding, and output path to the task's spec.
4. Match the output spec exactly: apply the required rounding to floats and cast counts/items to int before writing, and reproduce the precise nesting and key names.
5. Read the written JSON file back and confirm its structure and types match the spec before declaring done.

## Sources

- [trajectory summary](../../summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md)
- [normalized JSON](trajectories/df2b08e4-openai-chat-completions.analysis.json)
