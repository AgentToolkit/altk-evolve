---
name: recall
description: Retrieves relevant entities from a knowledge base. Designed to be invoked automatically via hooks to inject context-appropriate entities before task execution.
---

# Entity Retrieval

## Overview

This skill retrieves relevant entities from a stored knowledge base based on the current task context. It loads all stored entities and presents them to Claude for relevance filtering.

## How It Works

1. Hook fires on user prompt submission
2. Script reads prompt from stdin (JSON with `prompt` field)
3. Loads all entities from the entities JSON file
4. Outputs formatted entities to stdout
5. Claude receives entities as additional context and applies relevant ones

## Entities Storage

Entities are stored in `.kaizen/entities.json` in the project root:

```json
{
  "entities": [
    {
      "content": "Use context managers for file operations",
      "rationale": "Ensures proper resource cleanup",
      "category": "strategy",
      "trigger": "When processing files or managing resources"
    }
  ]
}
```
