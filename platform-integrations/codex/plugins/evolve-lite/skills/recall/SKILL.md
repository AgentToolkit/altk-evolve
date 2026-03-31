---
name: recall
description: Retrieves relevant entities from the local Evolve knowledge base. Designed to be invoked automatically through a Codex UserPromptSubmit hook and manually when you want to inspect saved guidance.
---

# Entity Retrieval

## Overview

This skill retrieves relevant entities from the local Evolve knowledge base based on the current task context. It loads all stored entities and presents them to Codex as additional developer context.

## How It Works

1. If Codex hooks are enabled in `~/.codex/config.toml` with `[features] codex_hooks = true`, the Codex `UserPromptSubmit` hook runs before the prompt is sent.
2. The helper script reads the prompt JSON from stdin.
3. It loads stored entities from `.evolve/entities/`.
4. It prints formatted guidance to stdout.
5. Codex adds that text as extra developer context for the turn.

## Manual Use

Run this if you want to inspect the currently stored entities yourself:

```bash
printf '{"prompt":"Show stored Evolve entities"}' | python3 "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py"
```

If you prefer not to enable Codex hooks, invoke the installed `evolve-lite:recall` skill manually when you want the saved guidance surfaced in the current session.

## Entities Storage

Entities are stored as markdown files in `.evolve/entities/`, nested by type:

```text
.evolve/entities/
  guideline/
    use-context-managers-for-file-operations.md
    cache-api-responses-locally.md
```

Each file uses markdown with YAML frontmatter:

```markdown
---
type: guideline
trigger: When processing files or managing resources
---

Use context managers for file operations

## Rationale

Ensures proper resource cleanup
```
