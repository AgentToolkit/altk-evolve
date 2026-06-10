---
id: 6661f54f1ad7
type: guideline
trigger: Combining two tabular sources that must be matched on a date that appears in different formats or row orders across the files.
agent: bob
tags: [dates, data-transformation, csv, python]
sources:
  - trajectories/4590dea6-openai-chat-completions.analysis.json
related_summary: summaries/4590dea6-d8a4-45ed-8196-d91708abd60f.md
---

# Join two CSVs on a normalized parsed-date key

To join two CSVs that share a logical date but store it differently and in different row orders, parse each file's date into a canonical `date` object and key a dict by it, then iterate one dict and look up the other by that key. Do not rely on row order or raw string equality — build `{parsed_date: value}` for each file independently, then intersect the keys so only dates present in both contribute to the result.

## Rationale

Two files describing the same days can differ in row ordering and in textual date format, so positional zipping or string-equality joins silently mismatch or drop rows. Normalizing both sides to the same `date` object makes the key canonical, and a dict-keyed intersection joins correctly regardless of order while naturally excluding dates missing from either side.

## Sources

- [trajectory summary](../summaries/4590dea6-d8a4-45ed-8196-d91708abd60f.md)
- [normalized JSON](trajectories/4590dea6-openai-chat-completions.analysis.json)
