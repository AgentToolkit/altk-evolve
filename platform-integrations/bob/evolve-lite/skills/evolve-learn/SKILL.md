---
name: learn
description: Analyze the current conversation to extract actionable entities — proactive recommendations derived from errors, failures, and successful patterns.
---

# Entity Generator

## Overview

This skill analyzes the current conversation to extract actionable entities that would help on similar tasks in the future. It **prioritizes errors** — tool failures, exceptions, wrong approaches, retry loops — and transforms them into proactive recommendations that prevent those errors from recurring.

## Workflow

### Step 1: Analyze the Conversation

Identify from the current conversation:

- **Task/Request**: What was the user asking for?
- **What Worked**: Which approaches succeeded?
- **What Failed**: Which approaches didn't work and why?
- **Errors Encountered**: Tool failures, exceptions, permission errors, retry loops, dead ends, wrong initial approaches

### Step 2: Identify Errors and Root Causes

Scan for these error signals:

1. **Tool/command failures**: Non-zero exit codes, error messages, exceptions
2. **Permission/access errors**: "Permission denied", "not found", sandbox restrictions
3. **Wrong initial approach**: First attempt abandoned for a different strategy
4. **Retry loops**: Same action attempted multiple times before succeeding
5. **Missing prerequisites**: Dependencies, packages, configs discovered mid-task
6. **Silent failures**: Actions that appeared to succeed but produced wrong results

If no errors are found, extract entities from successful patterns instead.

### Step 3: Extract Entities

Extract 3-5 proactive entities. **Prioritize entities derived from errors.**

Principles:

1. **Reframe failures as proactive recommendations** — recommend what worked, not what to avoid
   - Bad: "If exiftool fails, use PIL instead"
   - Good: "In sandboxed environments, use Python libraries (PIL/Pillow) for image metadata extraction"

2. **Triggers should be situational context, not failure conditions**
   - Bad trigger: "When apt-get fails"
   - Good trigger: "When working in containerized/sandboxed environments"

3. **For retry loops, recommend the final working approach directly** — eliminate trial-and-error by encoding the answer

### Step 4: Save Entities

Output entities as JSON and pipe to the save script:

```bash
echo '{
  "entities": [
    {
      "content": "Proactive entity stating what TO DO",
      "rationale": "Why this approach works better",
      "type": "guideline",
      "trigger": "Situational context when this applies"
    }
  ]
}' | python3 .bob/skills/evolve-learn/scripts/save_entities.py
```

The script will:
- Find or create the entities directory (`.evolve/entities/`)
- Write each entity as a markdown file in `{type}/` subdirectories
- Deduplicate against existing entities
- Display confirmation with the total count

## Best Practices

1. **Prioritize error-derived entities**: Errors are the highest-signal source of learnings
2. **One error, one entity**: Each distinct error should produce one prevention entity
3. **Be specific and actionable**: State what to do, not what to avoid
4. **Include rationale**: Explain why the approach works
5. **Use situational triggers**: Context-based, not failure-based
6. **Limit to 3-5 entities**: Focus on the most impactful learnings
7. **When more than 5 errors exist**: Merge errors with the same root cause, rank by severity > frequency > user impact, then keep the top 3-5
