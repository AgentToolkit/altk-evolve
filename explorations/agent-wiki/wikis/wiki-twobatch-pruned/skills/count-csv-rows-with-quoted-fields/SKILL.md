---
id: skill:count-csv-rows-with-quoted-fields
type: skill
name: count-csv-rows-with-quoted-fields
description: Count CSV rows whose any field contains a literal comma using stdlib `csv.reader` with `newline=''`.
trigger: see SKILL.md
agent: claude-code
sources:
  - trajectories/claude_md_strong__trial-1__ae7be18d-661a-4b5c-b87c-0b32f977ecb4.json
related_summary: summaries/ae7be18d-661a-4b5c-b87c-0b32f977ecb4.md
verified_at: 2026-06-09
tags: [parsing, csv, stdlib, rfc4180]
---

# Count Csv Rows With Quoted Fields

## Overview

Count CSV rows whose any field contains a literal comma using stdlib `csv.reader` with `newline=`.

## When To Use

- see SKILL.md

## Workflow

1. Run `bash <wiki>/skills/count-csv-rows-with-quoted-fields/scripts/run.sh ...`

## Sources

- [trajectory summary](../../summaries/ae7be18d-661a-4b5c-b87c-0b32f977ecb4.md)
- [normalized JSON](trajectories/claude_md_strong__trial-1__ae7be18d-661a-4b5c-b87c-0b32f977ecb4.json)
