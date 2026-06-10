---
type: cluster
slug: let-the-system-tell-you-dont-assume
title: Let the system tell you — never trust assumptions or success prints
tags: [empiricism, verification, debugging, assumptions]
verified_at: 2026-06-10
members:
  - id: 716da6023763
    link: probe-the-runtime-before-assuming__716da6023763.md
  - id: eaaa989db78a
    link: re-run-the-failing-command-after-every__eaaa989db78a.md
  - id: 4be167482caa
    link: verify-the-written-output-by-reading-it__4be167482caa.md
priority: high
---

# Let the system tell you — never trust assumptions or success prints

A recurring failure mode is building on belief instead of ground truth: assuming a library is installed, assuming a repair worked, assuming a script that printed 'done' actually produced the required output. Each assumption that goes unchecked can silently invalidate everything built on top of it.

The shared discipline is to make the system itself the source of truth at every stage — before you start (probe what's actually installed), during repair (re-run the failing command after each fix), and after producing a deliverable (read the file back and check its shape). Let the first real failure, not a guess, dictate the next step.

## Takeaway

Replace assumptions with cheap empirical checks. Before planning around a package, probe it (`python3 -c 'import pandas'`) and treat a bare base as stdlib-only until proven otherwise. After each repair step, immediately re-run the exact failing command and treat the fix as unproven until the symptom is gone. After a script reports success, read its output file back and confirm structure, key names, nesting, rounding, and int-vs-float against the spec — a clean exit code only proves it ran, not that it is correct.

## Members

These guidelines are kept as separate pages for full provenance back to their source trajectories. The cluster references them; nothing is moved or merged.

### [Probe the runtime before assuming libraries](probe-the-runtime-before-assuming__716da6023763.md)

- **id:** `716da6023763`
- **trigger:** Starting a data-processing or scripting task inside a minimal/unknown container where required third-party packages may not be installed.
- **source:** [d76ff7d9-9088-](../summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md)

> In a bare container, run the actual conversion attempt or an import probe (e.g. `python3 -c 'import pandas'`) before building a plan around a library. Treat a fresh Debian/Ubuntu base as having only the stdlib until proven otherwise, and let the first failure tell you exactly what to install rather…

### [Re-run the failing command after every fix](re-run-the-failing-command-after-every__eaaa989db78a.md)

- **id:** `eaaa989db78a`
- **trigger:** Iteratively repairing a broken tool or environment with multiple candidate causes.
- **source:** [93c78e3d-76ab-](../summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md)

> After each repair step, immediately re-run the exact command that was failing (here, `pip3 --version`) rather than assuming the change worked. Treat a fix as unproven until the original symptom is gone. This turns each attempt into a fast falsification check and stops a wrong theory from being…

### [Verify the written output by reading it back](verify-the-written-output-by-reading-it__4be167482caa.md)

- **id:** `4be167482caa`
- **trigger:** A task's deliverable is a file with a precise required structure, and a script has just written it.
- **source:** [df2b08e4-7853-](../summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md)

> After a script reports success and writes a result file, read the file back (`docker exec <container> cat /app/aggregates.json`) and confirm it matches the required structure and types before declaring the task done. Check that nesting, key names, rounding, and integer-vs-float types match the spec…
