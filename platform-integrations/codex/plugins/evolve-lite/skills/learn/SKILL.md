---
name: learn
description: Extract actionable entities from Codex conversation trajectories. Systematically identifies errors, failures, and inefficiencies to generate proactive entities that prevent them from recurring.
---

# Entity Generator

## Overview

This skill analyzes the current Codex conversation to extract actionable entities that would help on similar tasks in the future. It **prioritizes errors encountered during the conversation** such as tool failures, exceptions, wrong approaches, and retry loops, then turns them into proactive recommendations that prevent them from recurring.

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

1. **Reframe failures as proactive recommendations**
   If an approach failed due to permissions, recommend the alternative first.

2. **Focus on what worked, stated as the primary approach**
   Bad: "If exiftool fails, use PIL instead"
   Good: "In sandboxed environments, use Python libraries like PIL or Pillow for image metadata extraction"

3. **Triggers should be situational context, not failure conditions**
   Bad trigger: "When apt-get fails"
   Good trigger: "When working in containerized or sandboxed environments"

4. **For retry loops, recommend the final working approach as the starting point**
   If three variations were tried before one worked, the entity should recommend the working variation directly.

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

#### Method 1: Direct Pipe

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

## Best Practices

1. Prioritize error-derived entities first.
2. Keep entities specific and actionable.
3. Include rationale so the future agent understands why the guidance matters.
4. Use situational triggers instead of failure-based triggers.
5. Limit output to the 3-5 most valuable entities.
