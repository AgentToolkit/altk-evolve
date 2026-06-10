---
type: section-index
section: guidelines
verified_at: 2026-06-10
count: 8
atomic: 6
clusters: 2
---

# Guidelines

Atomic, trigger-tagged lessons plus aggregator **cluster pages** that group related variants. Cluster pages have the suffix `__cluster.md` and are recall-preferred — when a cluster and its members both match a query, the cluster wins. Members carry a `superseded_by:` field pointing at their cluster.

## Clusters (prefer these first)

- **[Cross the host/container boundary with docker exec](cross-the-host-container-boundary-with-docker-exec__cluster.md)** `cluster:cross-the-host-container-boundary-with-docker-exec` — `tags: docker, container, filesystem, shell` (2 members)
- **[Let the system tell you — never trust assumptions or success prints](let-the-system-tell-you-dont-assume__cluster.md)** `cluster:let-the-system-tell-you-dont-assume` — `tags: empiricism, verification, debugging, assumptions` (3 members)

## Atomic guidelines, alphabetical

- **[Get pandas from apt and pyarrow from pip](get-pandas-from-apt-and-pyarrow-from-pip__1cd395fcf4f2.md)** `1cd395fcf4f2`
  - For CSV-to-Parquet on Debian/Ubuntu, install pandas via `apt install -y python3-pandas` (it ships in the distro repos) but obtain the…
- **[Heredoc Python scripts into the container](heredoc-python-scripts-into-the__3ef4ed4a5952.md)** `3ef4ed4a5952` [→ cluster: cross-the-host-container-boundary-with-docker-exec](cross-the-host-container-boundary-with-docker-exec__cluster.md)
  - When a task confines you to a Docker container and needs more logic than a one-liner (multi-file aggregation, parsing, grouping), write the…
- **[Probe the runtime before assuming libraries](probe-the-runtime-before-assuming__716da6023763.md)** `716da6023763` [→ cluster: let-the-system-tell-you-dont-assume](let-the-system-tell-you-dont-assume__cluster.md)
  - In a bare container, run the actual conversion attempt or an import probe (e.g. `python3 -c 'import pandas'`) before building a plan around…
- **[Re-run the failing command after every fix](re-run-the-failing-command-after-every__eaaa989db78a.md)** `eaaa989db78a` [→ cluster: let-the-system-tell-you-dont-assume](let-the-system-tell-you-dont-assume__cluster.md)
  - After each repair step, immediately re-run the exact command that was failing (here, `pip3 --version`) rather than assuming the change…
- **[Read in-container files via docker exec cat](read-in-container-files-via-docker-exec__f35c351a3403.md)** `f35c351a3403` [→ cluster: cross-the-host-container-boundary-with-docker-exec](cross-the-host-container-boundary-with-docker-exec__cluster.md)
  - When the task runs inside a Docker container and the host-side file reader is sandboxed to the workspace, do not try to read container…
- **[Verify the written output by reading it back](verify-the-written-output-by-reading-it__4be167482caa.md)** `4be167482caa` [→ cluster: let-the-system-tell-you-dont-assume](let-the-system-tell-you-dont-assume__cluster.md)
  - After a script reports success and writes a result file, read the file back (`docker exec <container> cat /app/aggregates.json`) and…

## By tag

### `containers`

- [Heredoc Python scripts into the container](heredoc-python-scripts-into-the__3ef4ed4a5952.md) `3ef4ed4a5952`
- [Read in-container files via docker exec cat](read-in-container-files-via-docker-exec__f35c351a3403.md) `f35c351a3403`
- [Verify the written output by reading it back](verify-the-written-output-by-reading-it__4be167482caa.md) `4be167482caa`

### `dependencies`

- [Get pandas from apt and pyarrow from pip](get-pandas-from-apt-and-pyarrow-from-pip__1cd395fcf4f2.md) `1cd395fcf4f2`
- [Probe the runtime before assuming libraries](probe-the-runtime-before-assuming__716da6023763.md) `716da6023763`

### `docker`

- [Heredoc Python scripts into the container](heredoc-python-scripts-into-the__3ef4ed4a5952.md) `3ef4ed4a5952`
- [Read in-container files via docker exec cat](read-in-container-files-via-docker-exec__f35c351a3403.md) `f35c351a3403`

### `pandas`

- [Get pandas from apt and pyarrow from pip](get-pandas-from-apt-and-pyarrow-from-pip__1cd395fcf4f2.md) `1cd395fcf4f2`
- [Probe the runtime before assuming libraries](probe-the-runtime-before-assuming__716da6023763.md) `716da6023763`

### `python`

- [Heredoc Python scripts into the container](heredoc-python-scripts-into-the__3ef4ed4a5952.md) `3ef4ed4a5952`
- [Probe the runtime before assuming libraries](probe-the-runtime-before-assuming__716da6023763.md) `716da6023763`

### `verification`

- [Re-run the failing command after every fix](re-run-the-failing-command-after-every__eaaa989db78a.md) `eaaa989db78a`
- [Verify the written output by reading it back](verify-the-written-output-by-reading-it__4be167482caa.md) `4be167482caa`


## Recall roll-up

Cross-summary tally of `recalled_guidelines:` blocks. Rows are alphabetical by guideline title. A row of zeros means the guideline has been contributed by a session but never recalled by another.

| Guideline | Total | followed | ignored | contradicted | harmful |
|-----------|------:|---------:|--------:|-------------:|--------:|
| [Get pandas from apt and pyarrow from pip](get-pandas-from-apt-and-pyarrow-from-pip__1cd395fcf4f2.md) | 0 | 0 | 0 | 0 | 0 |
| [Heredoc Python scripts into the container](heredoc-python-scripts-into-the__3ef4ed4a5952.md) | 0 | 0 | 0 | 0 | 0 |
| [Probe the runtime before assuming libraries](probe-the-runtime-before-assuming__716da6023763.md) | 0 | 0 | 0 | 0 | 0 |
| [Re-run the failing command after every fix](re-run-the-failing-command-after-every__eaaa989db78a.md) | 0 | 0 | 0 | 0 | 0 |
| [Read in-container files via docker exec cat](read-in-container-files-via-docker-exec__f35c351a3403.md) | 0 | 0 | 0 | 0 | 0 |
| [Verify the written output by reading it back](verify-the-written-output-by-reading-it__4be167482caa.md) | 0 | 0 | 0 | 0 | 0 |

## Pages, by priority

Unified roll-up across clusters + atomic guidelines. Priority is computed each catalog run from recall counts and cluster membership (not authored). Rows sort by tier (`high` → `disputed` → `weak` → `normal` → `low` → `unvalidated`), then alphabetical within tier.

| Title | Kind | Priority | Trigger | Tags | Cluster | Recall (T / f / i / c / h) | Verified at |
|-------|------|----------|---------|------|---------|---------------------------:|-------------|
| [Cross the host/container boundary with docker exec](cross-the-host-container-boundary-with-docker-exec__cluster.md) | cluster | **high** | — | docker, container, filesystem, shell | — | — | 2026-06-10 |
| [Let the system tell you — never trust assumptions or success prints](let-the-system-tell-you-dont-assume__cluster.md) | cluster | **high** | — | empiricism, verification, debugging, assumptions | — | — | 2026-06-10 |
| [Heredoc Python scripts into the container](heredoc-python-scripts-into-the__3ef4ed4a5952.md) | atomic | **low** | The task runs inside a Docker container and needs a non-trivial program (mult… | docker, containers, python, scripting | cross-the-host-container-boundary-with-docker-exec | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Probe the runtime before assuming libraries](probe-the-runtime-before-assuming__716da6023763.md) | atomic | **low** | Starting a data-processing or scripting task inside a minimal/unknown contain… | minimal-env, python, pandas, dependencies, container | let-the-system-tell-you-dont-assume | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Re-run the failing command after every fix](re-run-the-failing-command-after-every__eaaa989db78a.md) | atomic | **low** | Iteratively repairing a broken tool or environment with multiple candidate ca… | debugging, verification, diagnosis, iteration | let-the-system-tell-you-dont-assume | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Read in-container files via docker exec cat](read-in-container-files-via-docker-exec__f35c351a3403.md) | atomic | **low** | The task specifies a Docker container and references files by an in-container… | docker, containers, file-access | cross-the-host-container-boundary-with-docker-exec | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Verify the written output by reading it back](verify-the-written-output-by-reading-it__4be167482caa.md) | atomic | **low** | A task's deliverable is a file with a precise required structure, and a scrip… | verification, containers, data-transformation | let-the-system-tell-you-dont-assume | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Get pandas from apt and pyarrow from pip](get-pandas-from-apt-and-pyarrow-from-pip__1cd395fcf4f2.md) | atomic | **unvalidated** | Setting up a pandas-based Parquet read/write pipeline on a Debian/Ubuntu host… | pandas, pyarrow, parquet, apt, pip, dependencies | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
