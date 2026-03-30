---
name: recall
description: Retrieves relevant entities from a knowledge base to inject context-appropriate entities before task execution.
---

# Entity Retrieval

## Overview

This skill retrieves relevant entities from a stored knowledge base based on the current task context. Read all stored entities from the entities directory and apply any relevant ones to the current task.

## How It Works

1. List all `.md` files under `.evolve/entities/` and its subdirectories
2. Read each file — the YAML frontmatter contains `type` and `trigger`, the body contains the entity content and rationale
3. Review each entity for relevance to the current task
4. Apply relevant entities as additional context for your work

## Entities Storage

Entities are stored as individual markdown files in `.evolve/entities/`, nested by type:

```
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
