---
name: learn
description: Extract actionable entities from Codex conversation trajectories. Systematically identifies errors, failures, and inefficiencies to generate proactive entities that prevent them from recurring.
---

# Entity Generator

## Overview

This skill analyzes the current Codex conversation to extract actionable entities that would help on similar tasks in the future. It **prioritizes errors encountered during the conversation** - tool failures, exceptions, wrong approaches, retry loops - and transforms them into proactive recommendations that prevent those errors from recurring.

## Workflow

### Step 1: Analyze the Conversation

Identify from your current conversation:

- **Task/Request**: What was the user asking for?
- **Steps Taken**: What reasoning, actions, and observations occurred?
- **What Worked**: Which approaches succeeded?
- **What Failed**: Which approaches did not work and why?
- **Errors Encountered**: Tool failures, exceptions, permission errors, retry loops, dead ends, and wrong initial approaches

### Step 2: Identify Errors and Root Causes

Scan the conversation for these error signals:

1. **Tool or command failures**: Non-zero exit codes, error messages, exceptions, stack traces
2. **Permission or access errors**: "Permission denied", "not found", sandbox restrictions
3. **Wrong initial approach**: First attempt abandoned in favor of a different strategy
4. **Retry loops**: Same action attempted multiple times with variations before succeeding
5. **Missing prerequisites**: Missing dependencies, packages, or configs discovered mid-task
6. **Silent failures**: Actions that appeared to succeed but produced wrong results

For each error found, document:

| | Error Example | Root Cause | Resolution | Prevention Guideline |
|---|---|---|---|---|
| 1 | `exiftool: command not found` | System tool unavailable in sandbox | Switched to Python PIL | Use PIL for image metadata in sandboxed environments |
| 2 | `git push` rejected (no upstream) | Branch not tracked to remote | Added `-u origin branch` | Always set upstream when pushing a new branch |
| 3 | Tried regex parsing of HTML, got wrong results | Regex cannot handle nested tags | Switched to BeautifulSoup | Use a proper HTML parser, never regex |

If no errors are found, continue to Step 3 and extract entities from successful patterns.

### Step 3: Extract Entities

Extract 3-5 proactive entities. **Prioritize entities derived from errors identified in Step 2.**

Follow these principles:

1. **Reframe failures as proactive recommendations:**
   - If an approach failed due to permissions, recommend the alternative first
   - If a system tool was unavailable, recommend what worked instead
   - If an approach hit environment constraints, recommend the constraint-aware approach

2. **Focus on what worked, stated as the primary approach:**
   - Bad: "If exiftool fails, use PIL instead"
   - Good: "In sandboxed environments, use Python libraries like PIL or Pillow for image metadata extraction"

3. **Triggers should be situational context, not failure conditions:**
   - Bad trigger: "When apt-get fails"
   - Good trigger: "When working in containerized or sandboxed environments"

4. **For retry loops, recommend the final working approach as the starting point:**
   - If three variations were tried before one worked, the entity should recommend the working variation directly
   - Eliminate the trial and error by encoding the answer

### Step 4: Output Entities JSON

Output entities in this JSON format:

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

After generating the entities JSON, save them using the helper script:

#### Method 1: Direct Pipe (Recommended)

```bash
echo '<your-json-output>' | python3 "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/plugins/evolve-lite/skills/learn/scripts/save_entities.py"
```

#### Method 2: From File

```bash
cat entities.json | python3 "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/plugins/evolve-lite/skills/learn/scripts/save_entities.py"
```

#### Method 3: Interactive

```bash
python3 "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/plugins/evolve-lite/skills/learn/scripts/save_entities.py"
```

The script will:

- Find or create the entities directory at `.evolve/entities/`
- Write each entity as a markdown file in `{type}/` subdirectories
- Deduplicate against existing entities
- Display confirmation with the total count

**Example:**

```bash
echo '{
  "entities": [
    {
      "content": "Use Python PIL or Pillow for image metadata extraction in sandboxed environments",
      "rationale": "System tools may not be available in sandboxed environments",
      "type": "guideline",
      "trigger": "When extracting image metadata in containerized or sandboxed environments"
    }
  ]
}' | python3 "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/plugins/evolve-lite/skills/learn/scripts/save_entities.py"
```

**Output:**

```text
Created new entities dir: /path/to/project/.evolve/entities
Added 1 new entity(ies). Total: 1
Entities stored in: /path/to/project/.evolve/entities
```

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
  "content": "Use Python PIL or Pillow for image metadata extraction in sandboxed environments",
  "rationale": "System tools like exiftool may not be available; Python libraries are a better default in constrained environments",
  "type": "guideline",
  "trigger": "When extracting image metadata in containerized or sandboxed environments"
}
```

### Error-Prevention Entity Examples

**From a retry loop** (tried three `git push` variations):

```json
{
  "content": "When pushing a new branch, use `git push -u origin <branch>` to set upstream tracking",
  "rationale": "Plain `git push` fails on new branches without upstream configured; `-u` sets it in one step",
  "type": "guideline",
  "trigger": "When pushing a newly created git branch for the first time"
}
```

**From a wrong initial approach** (tried regex, switched to a parser):

```json
{
  "content": "Use BeautifulSoup or lxml for HTML content extraction, never regex",
  "rationale": "Regex cannot reliably handle nested or malformed HTML; a proper parser handles edge cases",
  "type": "guideline",
  "trigger": "When extracting data from HTML documents or web pages"
}
```

**From a permission error** (`apt-get` blocked in a sandbox):

```json
{
  "content": "Install Python packages with `pip` or `uv` instead of system package managers in sandboxed environments",
  "rationale": "System package managers often require root access, while `pip` and `uv` work in user space",
  "type": "guideline",
  "trigger": "When installing dependencies in containerized or sandboxed environments"
}
```

## Best Practices

1. Prioritize error-derived entities first.
2. One distinct error should normally produce one prevention entity.
3. Keep entities specific and actionable.
4. Include rationale so the future agent understands why the guidance matters.
5. Use situational triggers instead of failure-based triggers.
6. Limit output to the 3-5 most valuable entities.
7. If more than five distinct errors appear, merge entities with the same root cause or fix, then rank the rest by severity, frequency, user impact, and recency before dropping the weakest ones.
