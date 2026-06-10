---
id: db39ac5cefdd
type: guideline
trigger: Parsing a date or timestamp column whose rows do not all share one format (mixed separators, orderings, or with/without a time component).
agent: bob
tags: [dates, parsing, python, data-transformation]
sources:
  - trajectories/4590dea6-openai-chat-completions.analysis.json
related_summary: summaries/4590dea6-d8a4-45ed-8196-d91708abd60f.md
---

# Parse heterogeneous dates with a strptime fallback list

When a column mixes several date formats (e.g. `04/19/2025 06:00:00` alongside `04-23-2025 06:00:00`), iterate over a list of candidate `strptime` formats and accept the first that parses without raising: loop the formats, `try datetime.strptime(s, fmt)` inside the loop, `break` on success and `continue` on `ValueError`. Enumerate every format you actually observe in the data before assuming a single one.

## Rationale

A single `strptime` call is locale- and format-rigid and raises `ValueError` on any string that does not match exactly, so one format cannot cover a heterogeneous column. Trying formats in sequence and catching `ValueError` normalizes every variant to one `datetime`/`date` object, which is the only reliable way to reconcile mixed separators and orderings before downstream comparison.

## Sources

- [trajectory summary](../summaries/4590dea6-d8a4-45ed-8196-d91708abd60f.md)
- [normalized JSON](trajectories/4590dea6-openai-chat-completions.analysis.json)
