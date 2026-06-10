---
id: skill:join-csvs-on-heterogeneous-date-keys
type: skill
name: join-csvs-on-heterogeneous-date-keys
description: Match and join two CSV files on a date column whose rows use inconsistent formats, by parsing each date with a strptime fallback list into a normalized date key, then aggregate across the joined rows.
trigger: Combining two tabular sources keyed on a date that appears in different string formats or row orders across the files (mixed separators, with/without a time component), where a naive string join would fail.
agent: bob
sources:
  - trajectories/4590dea6-openai-chat-completions.analysis.json
related_summary: summaries/4590dea6-d8a4-45ed-8196-d91708abd60f.md
verified_at: 2026-06-09
tags: [dates, data-transformation, csv, python, parsing]
---

# Join Csvs On Heterogeneous Date Keys

## Overview

Join two CSVs whose shared date column is stored in incompatible string formats by normalizing each date to a datetime.date key. Use a strptime fallback list to parse the inconsistent side, build a dict keyed by the parsed date, then look up the other file's rows against it and aggregate.

## When To Use

- Two CSV files must be matched on a date, but the date strings use different formats across files (e.g. ISO 2025-04-19 on one side, 04/19/2025 06:00:00 or 04-19-2025 06:00:00 on the other).
- Rows in the two files are not in the same order, so a positional zip would mismatch records.
- A single date column even within one file mixes separators or orderings, so one strptime format string is insufficient.
- You need a per-key aggregate (difference, sum, average) over the joined rows.

## Workflow

1. Inspect both files' first rows to learn each side's header and the exact date string layout. Note that the formats and row order may differ between the two files.
2. Read the well-formed side with csv.DictReader and build a dict keyed by datetime.strptime(date, fmt).date(), value = the numeric column as float.
3. Read the messy side with csv.DictReader. For each row, try each candidate format in a fallback list (e.g. ['%m/%d/%Y %H:%M:%S', '%m-%d-%Y %H:%M:%S', '%Y-%m-%d']) under try/except ValueError, breaking on the first that parses; key the result dict by that same datetime.date.
4. Join by iterating one dict's keys and looking up the matching date key in the other; skip dates absent from either side so only true matches contribute.
5. Compute the required aggregate over the matched pairs (here: average of per-date high-minus-low differences) and write only the bare number to the output file.
6. Run `python3 join_dates_csv.py <fileA.csv> <fileB.csv> <out.txt>` (pass the two CSV paths and the output path); adjust the column names and the format fallback list inside to match your data.
7. Verify by reading the output file back.

## Sources

- [trajectory summary](../../summaries/4590dea6-d8a4-45ed-8196-d91708abd60f.md)
- [normalized JSON](trajectories/4590dea6-openai-chat-completions.analysis.json)
