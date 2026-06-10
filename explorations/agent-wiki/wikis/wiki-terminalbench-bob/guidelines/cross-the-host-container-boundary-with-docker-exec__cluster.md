---
type: cluster
slug: cross-the-host-container-boundary-with-docker-exec
title: Cross the host/container boundary with docker exec
tags: [docker, container, filesystem, shell]
verified_at: 2026-06-10
members:
  - id: f35c351a3403
    link: read-in-container-files-via-docker-exec__f35c351a3403.md
  - id: 3ef4ed4a5952
    link: heredoc-python-scripts-into-the__3ef4ed4a5952.md
priority: high
---

# Cross the host/container boundary with docker exec

When a task is confined to a Docker container, the harness's host-side tools — the file reader, the file writer — are sandboxed to the workspace and cannot see or touch paths inside the container (e.g. /app/...). Any attempt to read_file an in-container path is rejected as outside the allowed directories.

The escape hatch is the shell tool plus `docker exec <container> ...`: every file read, file write, and program execution that must happen inside the container is routed through it. Reach for this immediately on seeing an in-container path, not after a failed host-side attempt.

## Takeaway

Treat `docker exec <container> ...` as the only path across the host/container boundary. Read in-container files with `docker exec <container> cat <path>`; write non-trivial programs in with a quoted-delimiter heredoc (`docker exec <container> bash -c 'cat > /app/x.py << '\''EOF'\''...EOF'`) and run them with `docker exec <container> python3 /app/x.py`. Do not waste a turn trying the host-side read_file/write tools on `/app/...` paths first.

## Members

These guidelines are kept as separate pages for full provenance back to their source trajectories. The cluster references them; nothing is moved or merged.

### [Read in-container files via docker exec cat](read-in-container-files-via-docker-exec__f35c351a3403.md)

- **id:** `f35c351a3403`
- **trigger:** The task specifies a Docker container and references files by an in-container path (e.g. /app/...) that the host-side file reader cannot resolve.
- **source:** [4590dea6-d8a4-](../summaries/4590dea6-d8a4-45ed-8196-d91708abd60f.md)

> When the task runs inside a Docker container and the host-side file reader is sandboxed to the workspace, do not try to read container paths like `/app/...` with the read_file tool — it will reject them as outside the allowed workspace directories. Instead, stream the file out with `docker exec…

### [Heredoc Python scripts into the container](heredoc-python-scripts-into-the__3ef4ed4a5952.md)

- **id:** `3ef4ed4a5952`
- **trigger:** The task runs inside a Docker container and needs a non-trivial program (multi-file processing, parsing, aggregation) rather than a single shell command.
- **source:** [df2b08e4-7853-](../summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md)

> When a task confines you to a Docker container and needs more logic than a one-liner (multi-file aggregation, parsing, grouping), write the script onto the container filesystem with a heredoc through the shell tool: `docker exec <container> bash -c 'cat > /app/script.py <<…
