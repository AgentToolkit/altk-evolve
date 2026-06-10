---
id: e0aaf94eeafc
type: guideline
trigger: pip installs are blocked by an externally-managed-environment error inside a short-lived container where a virtualenv is unnecessary.
agent: bob
tags: [pip, pep668, externally-managed, python, container]
sources:
  - trajectories/d76ff7d9-openai-chat-completions.analysis.json
related_summary: summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md
---

# Use break-system-packages for pip on PEP 668 hosts

When pip refuses to install with `error: externally-managed-environment` (PEP 668), pass `python3 -m pip install --break-system-packages <pkg>` to install system-wide in a disposable container. The flag is the intended escape hatch when no virtualenv is wanted and the environment is throwaway.

## Rationale

After pip was installed, `python3 -m pip install pyarrow` was blocked by PEP 668's externally-managed marker on Ubuntu Noble. Adding `--break-system-packages` let the pyarrow wheel download and install, after which the conversion succeeded. In an ephemeral container the usual caution about clobbering the system Python does not apply.

## Sources

- [trajectory summary](../summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md)
- [normalized JSON](trajectories/d76ff7d9-openai-chat-completions.analysis.json)
