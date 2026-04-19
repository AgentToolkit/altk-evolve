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
3. Loads entities from two sources: `.evolve/entities/` (private + subscribed) and `.evolve/public/` (your published guidelines)
4. Outputs formatted entities to stdout
5. Claude receives entities as additional context and applies relevant ones

## Entities Storage

Entities are loaded from two locations:

```
.evolve/entities/
  guideline/
    use-context-managers-for-file-operations.md   ← private
  subscribed/
    alice/
      guideline/
        alice-tip.md                              ← annotated [from: alice]

.evolve/public/
  guideline/
    published-tip.md                              ← your own public, no annotation
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
