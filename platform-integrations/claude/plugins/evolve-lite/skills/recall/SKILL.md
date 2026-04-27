---
name: recall
description: Retrieves relevant entities from a knowledge base. Designed to be invoked automatically via hooks to inject context-appropriate entities before task execution.
context: fork
---

# Entity Retrieval

## Overview

This skill retrieves relevant entities from a stored knowledge base based on the current task context. It loads all stored entities and presents them to Claude for relevance filtering.

## How It Works

1. Hook fires on user prompt submission
2. Script reads prompt from stdin (JSON with `prompt` field)
3. Loads entities from `.evolve/entities/` — covers private entities,
   subscribed read-scope repos, and write-scope publish targets (which
   are themselves cloned under `entities/subscribed/{repo}/`)
4. Outputs formatted entities to stdout
5. Claude receives entities as additional context and applies relevant ones

## Entities Storage

```text
.evolve/entities/
  guideline/
    use-context-managers-for-file-operations.md   ← private
  subscribed/
    memory/                                       ← write-scope clone (publishes land here)
      guideline/
        my-published-guideline.md
    alice/                                        ← read-scope clone
      guideline/
        alice-guideline.md                              ← annotated [from: alice]
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
