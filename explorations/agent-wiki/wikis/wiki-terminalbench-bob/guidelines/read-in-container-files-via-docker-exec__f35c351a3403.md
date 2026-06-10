---
id: f35c351a3403
type: guideline
trigger: The task specifies a Docker container and references files by an in-container path (e.g. /app/...) that the host-side file reader cannot resolve.
agent: bob
tags: [docker, containers, file-access]
sources:
  - trajectories/4590dea6-openai-chat-completions.analysis.json
related_summary: summaries/4590dea6-d8a4-45ed-8196-d91708abd60f.md
verified_at: 2026-06-10
cluster: cross-the-host-container-boundary-with-docker-exec__cluster.md
superseded_by: cross-the-host-container-boundary-with-docker-exec__cluster.md
---

# Read in-container files via docker exec cat

When the task runs inside a Docker container and the host-side file reader is sandboxed to the workspace, do not try to read container paths like `/app/...` with the read_file tool — it will reject them as outside the allowed workspace directories. Instead, stream the file out with `docker exec <container> cat <path>` (run through the shell command tool). Reach for this immediately for any `/app/` or other in-container path rather than after a failed read_file attempt.

## Rationale

The read_file tool resolves paths against the host workspace and the project temp dir, so a container path is never reachable and the call fails with a 'must be within one of the workspace directories' error. The container filesystem is only addressable through `docker exec`, and `cat` is the simplest read that returns the bytes to stdout where the agent can see them.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/4590dea6-d8a4-45ed-8196-d91708abd60f.md)
- [normalized JSON](trajectories/4590dea6-openai-chat-completions.analysis.json)
