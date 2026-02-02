---
name: learn
description: Extract actionable entities from conversation trajectories. Analyzes user requests, steps taken, successes and failures to generate proactive entities that help on similar future tasks.
---

# Entity Generator

## Overview

This skill analyzes conversation trajectories to extract actionable entities that would help on similar tasks in the future. It transforms reactive learnings (what failed) into proactive recommendations (what to do first).

## Workflow

### Step 1: Analyze the Conversation

Identify from your current conversation:

- **Task/Request**: What was the user asking for?
- **Steps Taken**: What reasoning, actions, and observations occurred?
- **What Worked**: Which approaches succeeded?
- **What Failed**: Which approaches didn't work and why?

### Step 2: Extract Entities

Extract 3-5 proactive entities following these principles:

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

### Step 3: Output Entities JSON

Output entities in the following JSON format:

```json
{
  "entities": [
    {
      "content": "Proactive entity stating what TO DO",
      "rationale": "Why this approach works better",
      "category": "strategy|recovery|optimization",
      "trigger": "Situational context when this applies"
    }
  ]
}
```

### Step 4: Save Entities

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
- Find or create the entities file (`.claude/entities.json`)
- Merge new entities with existing ones (avoiding duplicates)
- Display confirmation with the total count

**Example:**
```bash
echo '{
  "entities": [
    {
      "content": "Use Python PIL/Pillow for image metadata extraction",
      "rationale": "System tools may not be available in sandboxed environments",
      "category": "strategy",
      "trigger": "When extracting image metadata in containerized environments"
    }
  ]
}' | python3 ${CLAUDE_PLUGIN_ROOT}/skills/learn/scripts/save_entities.py
```

**Output:**
```text
Creating new file: /path/to/project/.claude/entities.json
Added 1 new entity(ies). Total: 1
Entities stored in: /path/to/project/.claude/entities.json
```

**Note:** Entities are also automatically saved when a conversation ends via the Stop hook.

## Entity Categories

- **strategy**: High-level approach or methodology choices
- **recovery**: Handling errors, edge cases, or unexpected situations
- **optimization**: Improving efficiency, performance, or code quality

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
  "category": "strategy",
  "trigger": "When extracting image metadata in containerized or sandboxed environments"
}
```

## Best Practices

1. **Be specific**: Generic entities are less useful than context-specific ones
2. **Be actionable**: Entities should clearly state what to do
3. **Include rationale**: Explain why the approach works
4. **Use situational triggers**: Context-based triggers are more useful than failure-based ones
5. **Limit to 3-5 entities**: Focus on the most impactful learnings
