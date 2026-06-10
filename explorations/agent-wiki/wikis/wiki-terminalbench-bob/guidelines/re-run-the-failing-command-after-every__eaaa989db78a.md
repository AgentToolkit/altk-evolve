---
id: eaaa989db78a
type: guideline
trigger: Iteratively repairing a broken tool or environment with multiple candidate causes.
agent: bob
tags: [debugging, verification, diagnosis, iteration]
sources:
  - trajectories/93c78e3d-openai-chat-completions.analysis.json
related_summary: summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md
verified_at: 2026-06-10
cluster: let-the-system-tell-you-dont-assume__cluster.md
superseded_by: let-the-system-tell-you-dont-assume__cluster.md
---

# Re-run the failing command after every fix

After each repair step, immediately re-run the exact command that was failing (here, `pip3 --version`) rather than assuming the change worked. Treat a fix as unproven until the original symptom is gone. This turns each attempt into a fast falsification check and stops a wrong theory from being built upon.

## Rationale

A fix that only looks correct — such as repointing a shebang — can leave the real fault untouched. Re-running the original failing command surfaced the unchanged traceback in one step, cheaply disproving the shebang theory and redirecting effort to the missing module before more time was sunk.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md)
- [normalized JSON](trajectories/93c78e3d-openai-chat-completions.analysis.json)
