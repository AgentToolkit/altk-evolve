---
id: skill:diagnose-and-bump-version-mismatch-on-upgrade
type: skill
name: diagnose-and-bump-version-mismatch-on-upgrade
description: Diagnose a dtype_backend / unexpected-keyword TypeError as a library-version mismatch, then upgrade the library and its engine dependency to compatible versions until the failing command runs.
trigger: A Python program fails with an 'unexpected keyword argument' TypeError (or a 'requires version X or newer' ImportError) because installed libraries predate the API the code targets; the fix is to bump the package and its peer dependencies, not to edit the code.
agent: bob
sources:
  - trajectories/be7c0ea4-openai-chat-completions.analysis.json
related_summary: summaries/be7c0ea4.md
verified_at: 2026-06-09
tags: [pandas, pyarrow, version-mismatch, dependencies, upgrade, diagnosis]
---

# Diagnose And Bump Version Mismatch On Upgrade

## Overview

Turn an 'unexpected keyword argument' TypeError into a version diagnosis: the keyword exists in a newer release than what is installed. Read the source to learn the minimum version the API needs, upgrade the package, then follow the upgrade chain to its engine dependency (e.g. pandas->pyarrow) and re-run the failing command after each bump.

## When To Use

- A command fails with `TypeError: <fn>() got an unexpected keyword argument '<kw>'` and the keyword is a real, newer API parameter (e.g. read_csv's dtype_backend, added in pandas 2.0).
- After upgrading a library you hit a follow-on `ImportError: requires version 'X' or newer of '<engine>'` — the upgraded package needs a newer peer/engine dependency.
- The code is correct and should not be edited; the environment is stale and the fix is to bump system-wide package versions to match the API the code targets.

## Workflow

1. Read the failing source file (e.g. `cat ./src/<module>.py`) to identify the keyword/API in use and any inline comment naming the required minimum version (e.g. 'requires pandas >= 2.0.0 for dtype_backend').
2. Print the currently installed version: `python -c "import <pkg>; print(<pkg>.__version__)"`. Confirm it predates the version that introduced the keyword.
3. Upgrade the primary package to the required floor: `pip install --upgrade "<pkg>>=<min-version>"`.
4. Re-run the original failing command. If it now fails with a different error, treat that as the next step — do not assume the first bump finished the job.
5. If the new error is `ImportError: Pandas requires version 'X' or newer of '<engine>'` (or any peer-dependency floor), upgrade that engine too: `pip install --upgrade "<engine>>=X"`.
6. Re-run the failing command a final time and confirm it exits 0 with the expected output. Stop only when the original command succeeds, not when an individual pip install succeeds.

## Sources

- [trajectory summary](../../summaries/be7c0ea4.md)
- [normalized JSON](trajectories/be7c0ea4-openai-chat-completions.analysis.json)
