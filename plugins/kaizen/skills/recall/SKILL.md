---
name: recall
description: Retrieves relevant guidelines from a knowledge base. Designed to be invoked automatically via hooks to inject context-appropriate guidelines before task execution.
---

# Guideline Retrieval

## Overview

This skill retrieves relevant guidelines from a stored knowledge base based on the current task context. It loads all stored guidelines and presents them to Claude for relevance filtering.

## How It Works

1. Hook fires on user prompt submission
2. Script reads prompt from stdin (JSON with `prompt` field)
3. Loads all guidelines from the guidelines JSON file
4. Outputs formatted guidelines to stdout
5. Claude receives guidelines as additional context and applies relevant ones

## Guidelines Storage

Guidelines are stored in `.claude/guidelines.json` in the project root:

```json
{
  "guidelines": [
    {
      "content": "Use context managers for file operations",
      "rationale": "Ensures proper resource cleanup",
      "category": "strategy",
      "trigger": "When processing files or managing resources"
    }
  ]
}
```
