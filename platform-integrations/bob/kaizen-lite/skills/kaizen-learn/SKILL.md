---
name: learn
description: Extract actionable entities from conversation trajectories. Systematically identifies errors, failures, and inefficiencies to generate proactive entities that prevent them from recurring.
---

# Entity Generator

## Overview

This skill analyzes conversation trajectories to extract actionable entities that would help on similar tasks in the future. It **prioritizes errors encountered during the conversation** — tool failures, exceptions, wrong approaches, retry loops — and transforms them into proactive recommendations that prevent those errors from recurring.

## Workflow

### Step 1: Analyze the Conversation

Identify from your current conversation:

- **Task/Request**: What was the user asking for?
- **Steps Taken**: What reasoning, actions, and observations occurred?
- **What Worked**: Which approaches succeeded?
- **What Failed**: Which approaches didn't work and why?
- **Errors Encountered**: Tool failures, exceptions, permission errors, retry loops, dead ends, and wrong initial approaches

### Step 2: Identify Errors and Root Causes

Scan the conversation for these error signals:

1. **Tool/command failures**: Non-zero exit codes, error messages, exceptions, stack traces
2. **Permission/access errors**: "Permission denied", "not found", sandbox restrictions
3. **Wrong initial approach**: First attempt abandoned in favor of a different strategy
4. **Retry loops**: Same action attempted multiple times with variations before succeeding
5. **Missing prerequisites**: Missing dependencies, packages, configs discovered mid-task
6. **Silent failures**: Actions that appeared to succeed but produced wrong results

For each error found, document:

| | Error Example | Root Cause | Resolution | Prevention Guideline |
|---|---|---|---|---|
| 1 | `exiftool: command not found` | System tool unavailable in sandbox | Switched to Python PIL | Use PIL for image metadata in sandboxed environments |
| 2 | `git push` rejected (no upstream) | Branch not tracked to remote | Added `-u origin branch` | Always set upstream when pushing a new branch |
| 3 | Tried regex parsing of HTML, got wrong results | Regex can't handle nested tags | Switched to BeautifulSoup | Use a proper HTML parser (BeautifulSoup/lxml), never regex |

> **If no errors are found**, proceed to Step 3 and extract entities from successful patterns.

### Step 3: Extract Entities

Extract 3-5 proactive entities. **Prioritize entities derived from errors identified in Step 2.**

Follow these principles:

1. **Reframe failures as proactive recommendations:**
   - If an approach failed due to permissions → recommend the alternative FIRST
   - If a system tool wasn't available → recommend what worked instead
   - If an approach hit environment constraints → recommend the constraint-aware approach

2. **Focus on what worked, stated as the primary approach:**
   - Bad: "If exiftool fails, use PIL instead"
   - Good: "In sandboxed environments, use Python libraries (PIL/Pillow) for image metadata extraction"

3. **Triggers should be situational context, not failure conditions:**
   - Bad trigger: "When apt-get fails"
   - Good trigger: "When working in containerized/sandboxed environments"

4. **For retry loops, recommend the final working approach as the starting point:**
   - If 3 variations were tried before one worked, the entity should recommend the working variation directly
   - Eliminate the trial-and-error by encoding the answer

### Step 4: Output Entities JSON

Output entities in the following JSON format:

```json
{
  "entities": [
    {
      "content": "Proactive entity stating what TO DO",
      "rationale": "Why this approach works better",
      "type": "guideline",
      "trigger": "Situational context when this applies"
    }
  ]
}
```

### Step 5: Save Entities

After generating the entities JSON, save them using the save_entities.py script:

#### Method 1: Direct Pipe (Recommended)

```bash
echo '<your-json-output>' | python3 ${CLAUDE_PLUGIN_ROOT}/skills/learn/scripts/save_entities.py
```

#### Method 2: From File

```bash
cat entities.json | python3 ${CLAUDE_PLUGIN_ROOT}/skills/learn/scripts/save_entities.py
```

#### Method 3: Interactive

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/learn/scripts/save_entities.py
# Then paste your JSON and press Ctrl+D
```

The script will:
- Find or create the entities directory (`.kaizen/entities/`)
- Write each entity as a markdown file in `{type}/` subdirectories
- Deduplicate against existing entities (avoiding duplicates)
- Display confirmation with the total count

**Example:**
```bash
echo '{
  "entities": [
    {
      "content": "Use Python PIL/Pillow for image metadata extraction",
      "rationale": "System tools may not be available in sandboxed environments",
      "type": "guideline",
      "trigger": "When extracting image metadata in containerized environments"
    }
  ]
}' | python3 ${CLAUDE_PLUGIN_ROOT}/skills/learn/scripts/save_entities.py
```

**Output:**
```text
Created new entities dir: /path/to/project/.kaizen/entities
Added 1 new entity(ies). Total: 1
Entities stored in: /path/to/project/.kaizen/entities
```

**Note:** Entities are also automatically saved when a conversation ends via the Stop hook.

## Examples

### Good vs Bad Entities

**BAD (reactive):**
```json
{
  "content": "Fall back to Python PIL when exiftool is not available",
  "trigger": "When exiftool command fails"
}
```

**GOOD (proactive):**
```json
{
  "content": "Use Python PIL/Pillow for image metadata extraction in sandboxed environments",
  "rationale": "System tools like exiftool may not be available; PIL is always installable via pip",
  "type": "guideline",
  "trigger": "When extracting image metadata in containerized or sandboxed environments"
}
```

### Error-Prevention Entity Examples

**From a retry loop** (tried 3 git push variations):
```json
{
  "content": "When pushing a new branch, always use 'git push -u origin <branch>' to set upstream tracking",
  "rationale": "Plain 'git push' fails on new branches without upstream configured; -u sets it in one step",
  "type": "guideline",
  "trigger": "When pushing a newly created git branch for the first time"
}
```

**From a wrong initial approach** (tried regex, switched to parser):
```json
{
  "content": "Use BeautifulSoup or lxml for HTML content extraction, never regex",
  "rationale": "Regex cannot reliably handle nested/malformed HTML; a proper parser handles edge cases",
  "type": "guideline",
  "trigger": "When extracting data from HTML documents or web pages"
}
```

**From a permission error** (apt-get blocked in sandbox):
```json
{
  "content": "Install Python packages with pip/uv instead of system package managers in sandboxed environments",
  "rationale": "apt-get and brew require root/sudo which sandboxed environments block; pip works in user space",
  "type": "guideline",
  "trigger": "When installing dependencies in containerized or sandboxed environments"
}
```

## Best Practices

1. **Prioritize error-derived entities**: Errors are the highest-signal source of learnings — extract entities from them first
2. **One error, one entity**: Each distinct error should produce exactly one prevention entity
3. **Be specific**: Generic entities are less useful than context-specific ones
4. **Be actionable**: Entities should clearly state what to do
5. **Include rationale**: Explain why the approach works
6. **Use situational triggers**: Context-based triggers are more useful than failure-based ones
7. **Limit to 3-5 entities**: Focus on the most impactful learnings
8. **Resolving Rules 2 vs 7**: When more than 5 distinct errors are found, start from Rule 2 (one error, one entity) then reduce to 3-5 entities using these steps:
   - **Merge**: Combine errors with the same root cause or fix into a single prevention entity
   - **Rank**: Select among remaining entities by severity > frequency > user impact > recency
   - **Drop**: Discard lowest-ranked entities that exceed the cap

   *Example*: A session hits 8 errors — 3 timeout variants (connect, read, DNS), 2 auth failures (expired token, missing header), 1 file-not-found, 1 permission error, 1 import error. Apply: merge the 3 timeouts into one network-resilience entity and the 2 auth failures into one auth-validation entity, then rank the resulting 5 entities (network-resilience, auth-validation, file-not-found, permission, import) and keep the top 3-5.
