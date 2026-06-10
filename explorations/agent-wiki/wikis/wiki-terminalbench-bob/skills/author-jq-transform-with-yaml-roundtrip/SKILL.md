---
id: skill:author-jq-transform-with-yaml-roundtrip
type: skill
name: author-jq-transform-with-yaml-roundtrip
description: Write a single jq pipeline that filters, reshapes, formats and sorts an array of records, then persist the exact filter and flags as a round-trippable jq_args.yaml plus a cmd.sh, so a checker can reconstruct and re-run the command.
trigger: "A data-transformation task asks you to produce an output file with jq only (no Python/Ruby), AND to also save the filter and its arguments to a YAML file (filter: + args: list) and/or a cmd.sh that reproduces the run."
agent: bob
sources:
  - trajectories/d0e03862-openai-chat-completions.analysis.json
related_summary: summaries/d0e03862.md
verified_at: 2026-06-09
tags: [jq, json, dates, edge-cases, yaml, reproducibility, verification, data-transformation]
---

# Author Jq Transform With Yaml Roundtrip

## Overview

Build one jq pipeline to filter, reshape and sort an array of records in a single pass, then persist that filter and its flags as a round-trippable jq_args.yaml (and a cmd.sh) so an external checker can rebuild and re-run the exact command.

## When To Use

- The task mandates jq only (other languages prohibited) for a filter / rename / reformat / sort over a JSON array.
- You must also emit a YAML file with a `filter:` string and an `args:` list of complete jq flags (each flag+value as one string), used to reconstruct `jq $(yq -r '.args[]' f.yaml) "$(yq -r '.filter' f.yaml)" input > output`.
- Per-record transforms include date formatting (ISO 8601 -> YYYY-MM-DD), counts, first-element-or-null extraction, and key renaming.

## Workflow

1. Inspect the input first: `jq 'type, (.[0])' input.json` (or cat it) to confirm it is an array and learn the exact field names. Do not assume the schema from the prompt alone.
2. Compose ONE jq pipeline that does every requirement in a single pass. Pattern: `[.[] | select(.status == "active") | {user_id: .id, username, email, last_login: (.last_login | split("T")[0]), role_count: (.roles | length), primary_role: (.roles[0] // null)}] | sort_by(.username)`. Key building blocks: `select(...)` to filter; object-construction `{new: .old}` to rename/extract; `split("T")[0]` to turn an ISO 8601 timestamp into a YYYY-MM-DD date (no date plugin needed); `length` for counts; `.arr[0] // null` (the `//` alternative operator) so an empty/missing array yields JSON null, not an error; wrap in `[ ... ] | sort_by(.key)` to re-collect and sort. Pass formatting flags like `--indent 2` (or `--tab`) for the required output shape.
3. Run it and redirect to the required output path, then read back the head of the output to confirm filtering, date format, null handling and sort order are correct.
4. Persist the exact command to cmd.sh AND the filter+args to jq_args.yaml using the helper to avoid shell-quoting mistakes: `bash <wiki>/skills/author-jq-transform-with-yaml-roundtrip/scripts/persist_jq_args.sh '<FILTER>' '<INPUT>' '<OUTPUT>' '<CMD_SH>' '<YAML>' '--indent 2'`. The script writes cmd.sh and a jq_args.yaml whose `args:` list holds each complete flag (flag+value as a single string) and whose `filter:` holds only the filter expression — no file paths, no redirection.
5. Round-trip verify: rebuild the command from the YAML exactly as the checker will and diff it against your direct output: `jq $(yq -r '.args[]' jq_args.yaml) "$(yq -r '.filter' jq_args.yaml)" input.json | diff - output.json`. An empty diff confirms the persisted args reproduce the result.

## Sources

- [trajectory summary](../../summaries/d0e03862.md)
- [normalized JSON](trajectories/d0e03862-openai-chat-completions.analysis.json)
