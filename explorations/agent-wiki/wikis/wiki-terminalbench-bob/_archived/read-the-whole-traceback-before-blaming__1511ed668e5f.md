---
id: 1511ed668e5f
type: guideline
trigger: Diagnosing why an installed CLI wrapper script fails to launch on a host you control.
agent: bob
tags: [debugging, traceback, shebang, python, diagnosis]
sources:
  - trajectories/93c78e3d-openai-chat-completions.analysis.json
related_summary: summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md
---

# Read the whole traceback before blaming the shebang

Before editing a script's shebang to fix a launcher error, read the traceback to the final line. A `ModuleNotFoundError: No module named 'pip'` raised from inside the script means the interpreter started fine and the import failed — a shebang or interpreter-path problem would instead produce a shell error like "bad interpreter" or "No such file or directory" before any Python runs. Match the fix to the actual failing line, not to a plausible-looking first line.

## Rationale

Here the shebang pointed at `/usr/local/bin/python3.13` (a real binary, reachable via the `python3` symlink), so it was a red herring; rewriting it to `python3` changed nothing and the identical traceback reappeared. The last line of the trace named the true fault — a missing module — and reading it first would have skipped the wasted sed edit and re-test.

## Sources

- [trajectory summary](../summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md)
- [normalized JSON](trajectories/93c78e3d-openai-chat-completions.analysis.json)
