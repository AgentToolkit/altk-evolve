---
id: 3a8b875e4382
type: guideline
trigger: A task asks you to serialize a command's filter and arguments to a config file that a downstream tool will reassemble and execute.
agent: bob
tags: [jq, yaml, reproducibility, verification]
sources:
  - trajectories/d0e03862-openai-chat-completions.analysis.json
related_summary: summaries/d0e03862-30c5-49b6-9aef-b97dcea57dc0.md
---

# Persist jq filter and args as round-trippable YAML

When a task requires saving the jq invocation as a reusable YAML (one `filter:` string plus an `args:` list of flags), keep the filter as a single string and put each complete flag as one list entry (e.g. `- "--indent 2"`, `- "--argjson min_count 5"`). Exclude the input path, output redirection, and the `jq` command itself from `args`. Then validate the file by actually running the documented reconstruction command — `jq $(yq -r '.args[]' file.yaml) "$(yq -r '.filter' file.yaml)" input.json` — and confirm its output matches the directly-produced result before declaring done.

## Rationale

The YAML is consumed by a script that splices `args` and `filter` straight into a jq call, so any extra path or split flag breaks reconstruction. Running the exact yq-driven command is the only proof the persisted form is equivalent to what was run; eyeballing the YAML misses quoting and word-splitting bugs.

## Sources

- [trajectory summary](../summaries/d0e03862-30c5-49b6-9aef-b97dcea57dc0.md)
- [normalized JSON](trajectories/d0e03862-openai-chat-completions.analysis.json)
