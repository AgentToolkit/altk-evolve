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
3. It emits a minimal manifest from `.evolve/entities/` and `.evolve/public/` containing only `path`, `type`, and `trigger`
4. Claude uses that manifest to decide which full entity files to read on demand
5. If the hook is not active, this skill remains the full manual fallback: inspect the entity files directly, read the relevant ones, and summarize what applies

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

Automatic hook output is manifest-first. Each manifest entry contains only:

```json
{"path": ".evolve/entities/guideline/use-context-managers-for-file-operations.md", "type": "guideline", "trigger": "When processing files or managing resources"}
```

Each file still uses markdown with YAML frontmatter:

```markdown
---
type: guideline
trigger: When processing files or managing resources
---

Use context managers for file operations

## Rationale

Ensures proper resource cleanup
```
