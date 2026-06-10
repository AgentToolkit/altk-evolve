---
id: 1cd395fcf4f2
type: guideline
trigger: Setting up a pandas-based Parquet read/write pipeline on a Debian/Ubuntu host that lacks the data stack.
agent: bob
tags: [pandas, pyarrow, parquet, apt, pip, dependencies]
sources:
  - trajectories/d76ff7d9-openai-chat-completions.analysis.json
related_summary: summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md
verified_at: 2026-06-10
---

# Get pandas from apt and pyarrow from pip

For CSV-to-Parquet on Debian/Ubuntu, install pandas via `apt install -y python3-pandas` (it ships in the distro repos) but obtain the Parquet engine with pip, since `python3-pyarrow` is not a packaged apt name. Install pip first with `apt install -y python3-pip`, then add the engine with pip.

## Rationale

`apt install python3-pandas python3-pyarrow` failed with `E: Unable to locate package python3-pyarrow`, forcing a split: pandas resolved cleanly from apt, but pyarrow had no apt package and had to come from PyPI. Knowing the split up front avoids the failed combined install and the dead-end search for a nonexistent apt package.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/d76ff7d9-9088-4447-9a9a-1250ae3151eb.md)
- [normalized JSON](trajectories/d76ff7d9-openai-chat-completions.analysis.json)
