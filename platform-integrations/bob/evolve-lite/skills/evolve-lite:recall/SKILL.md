---
name: recall
description: Retrieves relevant entities from a knowledge base to inject context-appropriate entities before task execution.
---

# Entity Retrieval

## Overview

This skill retrieves relevant entities from a stored knowledge base based on the current task context. Read all stored entities from the entities directory and apply any relevant ones to the current task.

Entities can come from multiple sources:
- **Private entities**: Your own local entities (not shared)
- **Public entities**: Your own entities marked for sharing
- **Subscribed entities**: Entities from other users you've subscribed to

## How It Works

1. List all `.md` files under `.evolve/entities/`, `.evolve/public/`, and their subdirectories
2. Read each file — the YAML frontmatter contains `type` and `trigger`, the body contains the entity content and rationale
3. Review each entity for relevance to the current task
4. Apply relevant entities as additional context for your work

**Directory structure**:
- `.evolve/entities/guideline/` - Your private entities
- `.evolve/entities/subscribed/{name}/` - Mirrored entities from subscriptions
- `.evolve/public/guideline/` - Your published entities

## Usage

```bash
python3 scripts/retrieve_entities.py
```

This retrieves all entities from all sources (private, public, and subscribed).

## Entities Storage

Entities are stored as individual markdown files in `.evolve/entities/`, organized by source:

```text
.evolve/entities/
  guideline/                    # Private entities
    use-context-managers.md
  public/                       # Your published entities
    guideline/
      cache-api-responses.md
  subscribed/                   # Entities from others
    alice/
      guideline/
        error-handling.md
    bob-team/
      policy/
        code-review.md
```

Each file uses markdown with YAML frontmatter:

```markdown
---
type: guideline
trigger: When processing files or managing resources
visibility: private
owner: alice
---

Use context managers for file operations

## Rationale

Ensures proper resource cleanup
```

## Entity Annotations

Subscribed entities are annotated with their source:
```
- **[guideline]** [from: alice] Use context managers for file operations
  - _Rationale: Ensures proper resource cleanup_
  - _When: When processing files or managing resources_
```
