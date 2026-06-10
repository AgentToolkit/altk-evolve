---
id: 3ef4ed4a5952
type: guideline
trigger: The task runs inside a Docker container and needs a non-trivial program (multi-file processing, parsing, aggregation) rather than a single shell command.
agent: bob
tags: [docker, containers, python, scripting]
sources:
  - trajectories/df2b08e4-openai-chat-completions.analysis.json
related_summary: summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md
verified_at: 2026-06-10
cluster: cross-the-host-container-boundary-with-docker-exec__cluster.md
superseded_by: cross-the-host-container-boundary-with-docker-exec__cluster.md
---

# Heredoc Python scripts into the container

When a task confines you to a Docker container and needs more logic than a one-liner (multi-file aggregation, parsing, grouping), write the script onto the container filesystem with a heredoc through the shell tool: `docker exec <container> bash -c 'cat > /app/script.py << '\''EOF'\''\n...python...\nEOF'`, then run it with `docker exec <container> python3 /app/script.py`. Quote the heredoc delimiter (`'EOF'`) so the shell does not expand `$`, backticks, or other metacharacters inside the script body.

This keeps the whole program in the container where its input files live, avoids escaping a long inline `python3 -c` string, and lets you re-run or edit the same file across attempts.

## Rationale

The host file-writing tools are sandboxed to the workspace and cannot create files at in-container paths like `/app`, so the script must be materialized inside the container. A heredoc piped through `bash -c` writes the file in one shell step without per-line escaping, and a quoted delimiter prevents the outer shell from mangling Python that contains `$`, quotes, or backslashes.

## Used by

_(no recalls yet)_

## Sources

- [trajectory summary](../summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md)
- [normalized JSON](trajectories/df2b08e4-openai-chat-completions.analysis.json)
