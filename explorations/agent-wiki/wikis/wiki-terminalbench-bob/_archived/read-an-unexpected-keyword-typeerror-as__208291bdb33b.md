---
id: 208291bdb33b
type: guideline
trigger: A third-party library call fails with TypeError naming an unexpected keyword argument.
agent: bob
tags: [pandas, version-mismatch, diagnosis, typeerror, dependencies]
sources:
  - trajectories/be7c0ea4-openai-chat-completions.analysis.json
related_summary: summaries/be7c0ea4-f906-483a-82bc-4301ae3ef919.md
---

# Read an unexpected-keyword TypeError as a version mismatch

When a library call dies with `TypeError: <func>() got an unexpected keyword argument '<kw>'`, treat it as a version-too-old signal rather than a coding bug. The keyword exists in newer releases; the installed package predates it. Check the installed version, look up which release introduced that keyword, and upgrade to at least that release. Here `read_csv() got an unexpected keyword argument 'dtype_backend'` immediately pointed at pandas predating 2.0, and `pd.__version__` confirmed 1.3.0.

## Rationale

An unexpected-keyword TypeError almost never means the caller is wrong about the API — it means the running version is older than the API the caller targets. Reframing it as a version gap turns a confusing error into a one-step diagnosis (find the introducing release, upgrade past it) instead of a hunt through the call site.

## Sources

- [trajectory summary](../summaries/be7c0ea4-f906-483a-82bc-4301ae3ef919.md)
- [normalized JSON](trajectories/be7c0ea4-openai-chat-completions.analysis.json)
