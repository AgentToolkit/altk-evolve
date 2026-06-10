---
id: f69e16ac865b
type: guideline
trigger: A jq transformation needs an ISO 8601 datetime collapsed to a calendar date with no timezone shift or arithmetic.
agent: bob
tags: [jq, dates, data-transformation]
sources:
  - trajectories/d0e03862-openai-chat-completions.analysis.json
related_summary: summaries/d0e03862-30c5-49b6-9aef-b97dcea57dc0.md
---

# Format ISO timestamps to a date with split

To reduce an ISO 8601 timestamp like `2025-10-29T07:07:05.465787Z` to a `YYYY-MM-DD` date in jq, split on the literal `T` and take the first element: `(.last_login | split("T")[0])`. Reach for the date-math builtins (`fromdateiso8601`/`strftime`, `strptime`) only when you need timezone conversion or arithmetic, not for a plain string truncation.

## Rationale

The date portion of an ISO 8601 string is always the prefix before `T`, so a string split is exact and avoids the parsing pitfalls of `strptime` (which is locale/format sensitive and rejects fractional seconds in some jq builds). It is the smallest correct transformation for a same-day, UTC-only requirement.

## Sources

- [trajectory summary](../summaries/d0e03862-30c5-49b6-9aef-b97dcea57dc0.md)
- [normalized JSON](trajectories/d0e03862-openai-chat-completions.analysis.json)
