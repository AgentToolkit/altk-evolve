---
name: learn
description: Extract actionable guidelines from conversation trajectories. Analyzes user requests, steps taken, successes and failures to generate proactive guidelines that help on similar future tasks.
---

# Guideline Generator

## Overview

This skill analyzes conversation trajectories to extract actionable guidelines that would help on similar tasks in the future. It transforms reactive learnings (what failed) into proactive recommendations (what to do first).

## Workflow

### Step 1: Analyze the Conversation

Identify from your current conversation:

- **Task/Request**: What was the user asking for?
- **Steps Taken**: What reasoning, actions, and observations occurred?
- **What Worked**: Which approaches succeeded?
- **What Failed**: Which approaches didn't work and why?

### Step 2: Extract Guidelines

Extract 3-5 proactive guidelines following these principles:

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

### Step 3: Output Guidelines JSON

Output guidelines in the following JSON format:

```json
{
  "guidelines": [
    {
      "content": "Proactive guideline stating what TO DO",
      "rationale": "Why this approach works better",
      "category": "strategy|recovery|optimization",
      "trigger": "Situational context when this applies"
    }
  ]
}
```

### Step 4: Save Guidelines

After generating the guidelines JSON, save them using the save_guidelines.py script:

**Method 1: Direct Pipe (Recommended)**
```bash
echo '<your-json-output>' | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/save_guidelines.py
```

**Method 2: From File**
```bash
cat guidelines.json | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/save_guidelines.py
```

**Method 3: Interactive**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/save_guidelines.py
# Then paste your JSON and press Ctrl+D
```

The script will:
- Find or create the guidelines file (`.claude/guidelines.json`)
- Merge new guidelines with existing ones (avoiding duplicates)
- Display confirmation with the total count

**Example:**
```bash
echo '{
  "guidelines": [
    {
      "content": "Use Python PIL/Pillow for image metadata extraction",
      "rationale": "System tools may not be available in sandboxed environments",
      "category": "strategy",
      "trigger": "When extracting image metadata in containerized environments"
    }
  ]
}' | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/save_guidelines.py
```

**Output:**
```
Creating new file: /path/to/project/.claude/guidelines.json
Added 1 new guideline(s). Total: 1
Guidelines stored in: /path/to/project/.claude/guidelines.json
```

**Note:** Guidelines are also automatically saved when a conversation ends via the Stop hook.

## Guideline Categories

- **strategy**: High-level approach or methodology choices
- **recovery**: Handling errors, edge cases, or unexpected situations
- **optimization**: Improving efficiency, performance, or code quality

## Examples

### Good vs Bad Guidelines

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

1. **Be specific**: Generic guidelines are less useful than context-specific ones
2. **Be actionable**: Guidelines should clearly state what to do
3. **Include rationale**: Explain why the approach works
4. **Use situational triggers**: Context-based triggers are more useful than failure-based ones
5. **Limit to 3-5 guidelines**: Focus on the most impactful learnings
