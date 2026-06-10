---
id: skill:install-python-data-stack-on-pep668-debian
type: skill
name: install-python-data-stack-on-pep668-debian
description: Install pandas + pyarrow (and friends) on a minimal Debian/Ubuntu host that has no pip, an externally-managed (PEP 668) Python, and packages missing from apt — then convert CSV to Parquet (or similar pandas I/O).
trigger: A task needs a Python data library (pandas, pyarrow, numpy) on a bare Debian/Ubuntu box where `import` fails, `pip`/`pip3` is absent, `apt-get` may be missing (use `apt`), some wheels are not packaged in apt (e.g. python3-pyarrow), and a plain `pip install` is blocked by an externally-managed-environment / PEP 668 error.
agent: bob
sources:
  - trajectories/d76ff7d9-openai-chat-completions.analysis.json
related_summary: summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md
verified_at: 2026-06-09
tags: [python, pep668, pandas, pyarrow]
---

# Install Python Data Stack On Pep668 Debian

## Overview

Bootstraps the Python data stack on a minimal PEP 668 Debian/Ubuntu environment: prefer apt for what it carries (python3-pandas, python3-pip), then fall back to `pip install --break-system-packages` for wheels apt does not package (pyarrow). Includes the CSV-to-Parquet conversion the recipe was derived from.

## When To Use

- `import pandas` / `import pyarrow` fails with ModuleNotFoundError on a fresh container.
- `pip` and `pip3` are not on PATH and must be installed first.
- `apt-get` is absent but `apt` exists (use `apt` directly; it works non-interactively).
- A needed package is missing from apt (e.g. `E: Unable to locate package python3-pyarrow`).
- `pip install <pkg>` aborts with `error: externally-managed-environment` (PEP 668).

## Workflow

1. Inspect the input first (e.g. `head -20 /app/data.csv`) and confirm `python3` exists with `which python3`.
2. Probe the environment before installing: `which pip pip3` (often absent) and `which apt apt-get` (on recent Ubuntu only `apt` may exist).
3. Install what apt packages directly. pandas is in apt; pip usually is too: `apt update && apt install -y python3-pandas python3-pip`. Note `apt-get` may be missing — use `apt`; the 'unstable CLI' warning is harmless here.
4. Do NOT assume every wheel is in apt. `apt install -y python3-pyarrow` fails with `E: Unable to locate package python3-pyarrow`. For such packages, fall back to pip.
5. A plain `python3 -m pip install pyarrow` will fail with `error: externally-managed-environment` (PEP 668). Override it: `python3 -m pip install --break-system-packages pyarrow`.
6. Run the actual work. For CSV to Parquet: `python3 -c "import pandas as pd; pd.read_csv('IN.csv').to_parquet('OUT.parquet', engine='pyarrow', index=False)"`.
7. Verify the artifact exists and is non-empty: `ls -lh OUT.parquet`.
8. Or run the helper end-to-end: `bash <wiki>/skills/install-python-data-stack-on-pep668-debian/scripts/csv_to_parquet.sh /app/data.csv /app/data.parquet`.

## Sources

- [trajectory summary](../../summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md)
- [normalized JSON](trajectories/d76ff7d9-openai-chat-completions.analysis.json)
