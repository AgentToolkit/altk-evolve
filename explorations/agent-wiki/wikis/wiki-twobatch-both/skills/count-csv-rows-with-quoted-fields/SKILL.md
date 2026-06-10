---
id: skill:count-csv-rows-with-quoted-fields
type: skill
name: count-csv-rows-with-quoted-fields
description: Count CSV rows whose any field contains a literal comma (or other RFC-4180 special) using stdlib `csv.reader` with the load-bearing `newline=''` open argument.
trigger: When parsing CSV and a field might contain a comma, newline, or embedded quote — the naive `line.split(',')` overcounts; only `csv.reader` honors RFC 4180 quoting.
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__ae7be18d-661a-4b5c-b87c-0b32f977ecb4.json
related_summary: summaries/ae7be18d-661a-4b5c-b87c-0b32f977ecb4.md
verified_at: 2026-06-08
tags: [parsing, csv, stdlib, rfc4180]
---

# Count CSV rows with quoted fields

## Overview

Walk a CSV with stdlib `csv.reader` (opened with `newline=''` — required, otherwise embedded newlines inside quoted fields break row boundaries) and count rows that contain at least one comma in any field. The wrapper is one short script so you don't risk omitting `newline=''` by hand.

## When To Use

- You need a count or filter of CSV rows whose fields contain commas, quotes, or newlines
- Naive `awk -F,` / `cut -d,` would overcount because they don't honor RFC 4180 quoting

## Workflow

1. Run `bash <wiki>/skills/count-csv-rows-with-quoted-fields/scripts/run.sh <csv-path>`. The script prints the count.
2. If you need a different filter (specific column, multi-row aggregations), open the script and copy its `csv.reader(open(path, newline=''))` pattern — `newline=''` is the load-bearing argument; everything else is task-specific.

## Sources

- [trajectory summary](../../summaries/ae7be18d-661a-4b5c-b87c-0b32f977ecb4.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__ae7be18d-661a-4b5c-b87c-0b32f977ecb4.json)
