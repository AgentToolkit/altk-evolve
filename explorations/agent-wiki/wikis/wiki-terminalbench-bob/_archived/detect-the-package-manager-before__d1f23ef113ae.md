---
id: d1f23ef113ae
type: guideline
trigger: Needing to install system or Python packages in a container whose tooling layout is unknown.
agent: bob
tags: [minimal-env, apt, package-manager, dependencies, container]
sources:
  - trajectories/d76ff7d9-openai-chat-completions.analysis.json
related_summary: summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md
---

# Detect the package manager before installing

On Debian/Ubuntu minimal images, the package manager is usually `apt` while `apt-get`, `pip3`, and `pip` are often absent. Confirm what exists with a single `which apt apt-get apk yum dnf pip3` probe before issuing install commands, then drive installs through whatever is actually present.

## Rationale

The session burned several turns on `pip3 list` (exit 127), `apt-get update && apt-get install` (`bash: apt-get: command not found`), and `python3 -m pip install` (`No module named pip`) before discovering `/usr/bin/apt` was the only available manager. One upfront capability probe collapses that retry loop.

## Sources

- [trajectory summary](../summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md)
- [normalized JSON](trajectories/d76ff7d9-openai-chat-completions.analysis.json)
