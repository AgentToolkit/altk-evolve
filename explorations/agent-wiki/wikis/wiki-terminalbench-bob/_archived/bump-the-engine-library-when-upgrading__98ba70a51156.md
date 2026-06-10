---
id: 98ba70a51156
type: guideline
trigger: Upgrading pandas across a major version on code that uses the pyarrow/Arrow dtype backend or Parquet.
agent: bob
tags: [pandas, pyarrow, version-mismatch, dependencies, upgrade]
sources:
  - trajectories/be7c0ea4-openai-chat-completions.analysis.json
related_summary: summaries/be7c0ea4-f906-483a-82bc-4301ae3ef919.md
---

# Bump the engine library when upgrading pandas major versions

A major pandas upgrade (1.x to 2.x) raises the minimum version of its optional engines, so plan to upgrade pyarrow in the same operation rather than after the next failure. pandas 2.0 requires pyarrow >= 7.0.0, and a container carrying pyarrow 6.0.0 will pass the pandas upgrade cleanly yet still crash on the first read_csv that uses the Arrow backend. Upgrade both up front: `pip install --upgrade 'pandas>=2.0.0' 'pyarrow>=7.0.0'`.

## Rationale

pandas does not pin or pull its optional Parquet/Arrow engine, so an existing too-old pyarrow survives the pandas bump and only surfaces when Arrow-backed code actually runs. Here the post-upgrade run produced a fresh traceback from pyarrow 6.0.0, costing an extra diagnose-and-reinstall cycle that a combined upgrade would have avoided.

## Sources

- [trajectory summary](../summaries/be7c0ea4-f906-483a-82bc-4301ae3ef919.md)
- [normalized JSON](trajectories/be7c0ea4-openai-chat-completions.analysis.json)
