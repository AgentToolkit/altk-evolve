---
id: 716da6023763
type: guideline
trigger: Starting a data-processing or scripting task inside a minimal/unknown container where required third-party packages may not be installed.
agent: bob
tags: [minimal-env, python, pandas, dependencies, container]
sources:
  - trajectories/d76ff7d9-openai-chat-completions.analysis.json
related_summary: summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md
verified_at: 2026-06-10
cluster: let-the-system-tell-you-dont-assume__cluster.md
superseded_by: let-the-system-tell-you-dont-assume__cluster.md
---

# Probe the runtime before assuming libraries

In a bare container, run the actual conversion attempt or an import probe (e.g. `python3 -c 'import pandas'`) before building a plan around a library. Treat a fresh Debian/Ubuntu base as having only the stdlib until proven otherwise, and let the first failure tell you exactly what to install rather than guessing.

## Rationale

The session opened by piping a CSV straight through `pandas.read_csv(...).to_parquet(...)`, which failed with `ModuleNotFoundError: No module named 'pandas'`. A throwaway minimal base image carries no data-science stack, so optimistic one-shot commands waste a turn and produce a confusing traceback instead of a clear inventory.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md)
- [normalized JSON](trajectories/d76ff7d9-openai-chat-completions.analysis.json)
