---
name: kaizen-learn
description: Extract actionable entities from the completed conversation. Systematically identifies errors, failures, and inefficiencies to generate proactive entities that prevent them from recurring.
---

# Kaizen Learn Skill

## Overview

This skill analyzes your recent actions to extract actionable entities that would help on similar tasks in the future. It **prioritizes errors encountered during the conversation** — tool failures, exceptions, wrong approaches, retry loops — and transforms them into proactive recommendations that prevent those errors from recurring.

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

For each error found, clearly document the progression from failure to prevention:

| Error Example | Root Cause | Resolution | Prevention Guideline |
|---|---|---|---|
| `exiftool: command not found` | System tool unavailable | Switched to Python PIL | Use PIL for image metadata in sandboxed environments |
| `git push` rejected | Branch not tracked to remote | Added `-u origin branch` | Always set upstream when pushing a new branch |
| Tried regex parsing of HTML | Regex can't handle nested tags | Switched to BeautifulSoup | Use a proper HTML parser (BeautifulSoup/lxml), never regex |

> **If no errors are found**, proceed to Step 3 — but note that zero entities is a valid outcome for routine conversations.

### Step 2b: Quality Gate

Before extracting entities, every candidate insight must pass **all three** of these criteria:

1. **Non-obvious** — Would a competent LLM NOT already do this by default? Generic conversational behaviors (e.g., "answer directly," "clarify ambiguity," "execute commands when asked") are not worth saving.
2. **Environment or project-specific** — The insight encodes something about THIS codebase, THIS OS, THIS tool configuration, or THIS user's preferences — not general knowledge any LLM would already have.
3. **Derived from an actual mistake or discovery** — The insight was learned from a real failure, unexpected behavior, or non-trivial success in the conversation — not just from observing that things went smoothly.

If no candidates pass all three criteria, output an empty entities array (`{"entities": []}`). **Saving low-quality entities degrades the knowledge base over time.**

### Step 2c: Review Existing Entities

Before generating new entities, check what already exists to avoid near-duplicates:

```bash
python3 <path-to-kaizen-recall-dir>/scripts/get.py --type guideline --task "<brief summary>"
```
*(Use `python` if `python3` isn't found)*

If the insight you're about to save is already covered by an existing entity — even if worded differently — **do not create a near-duplicate**. Instead, only create a new entity if it adds genuinely new information not captured by any existing entity.

### Step 3: Extract Entities

Extract **0-2** proactive entities. **Zero is a valid answer.** If the conversation was routine with no errors, unexpected behavior, or non-obvious discoveries, output an empty entities array. **Prioritize entities derived from errors identified in Step 2.**

Follow these principles:
1. **Reframe failures as proactive recommendations:**
   - If an approach failed due to permissions → recommend the alternative FIRST
   - If a system tool wasn't available → recommend what worked instead
2. **Focus on what worked, stated as the primary approach:**
   - Bad: "If exiftool fails, use PIL instead"
   - Good: "In sandboxed environments, use Python libraries (PIL) for image metadata extraction"
3. **Triggers should be situational context, not failure conditions:**
   - Bad trigger: "When apt-get fails"
   - Good trigger: "When working in containerized environments"
4. **Map error-derived entities to categories:**
   - `strategy` — wrong approach was chosen → recommend the right approach from the start
   - `recovery` — a fallback chain was needed → start from the approach that worked
   - `optimization` — effort was wasted on retries/timeouts → eliminate the waste
   > If you find yourself categorizing everything as `strategy`, reconsider whether the entity is truly non-obvious. True strategy entities arise when a wrong approach was actually taken.
5. **Merge/Rank/Drop (For chaotic sessions)**: If you find many errors, apply this algorithm to get down to 0-2 entities:
   - **Merge**: Combine errors with the same root cause into a single prevention entity
   - **Rank**: Select among remaining entities by severity > frequency > user impact > recency
   - **Drop**: Discard lowest-ranked entities that exceed the 2-entity cap
6. **Do NOT generate entities like these** (too generic / obvious):
   - "Answer factual questions from knowledge" — any LLM already does this
   - "Clarify ambiguous user queries" — basic conversational behavior
   - "Execute commands when the user asks you to" — obvious
   - "Provide context with answers" — too vague, applies to everything
   - "For simple tasks, keep it simple" — truism
7. **DO generate entities like these** (specific, learned):
   - "Use `python3` instead of `python` on macOS — the `python` symlink doesn't exist by default" — environment-specific
   - "The kaizen save.py script reads from stdin only; do not pass CLI arguments" — project-specific, error-derived
   - "In sandboxed containers, `apt-get` is unavailable; use Python stdlib for system tasks" — recovery from a real constraint
   - "Copy skill directories with `cp -r` then update `custom_modes.yaml` references" — project workflow knowledge

### Step 4: Output Entities JSON

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

### Step 4b: Examples of Good vs Bad Entities

**BAD (reactive and generic):**
```json
{
  "content": "Fall back to Python PIL when exiftool is not available",
  "trigger": "When exiftool command fails"
}
```

**GOOD (proactive and situational):**
```json
{
  "content": "Use Python PIL/Pillow for image metadata extraction in sandboxed environments",
  "rationale": "System tools like exiftool may not be available; PIL is always installable via pip",
  "category": "strategy",
  "trigger": "When extracting image metadata in containerized or sandboxed environments"
}
```

### Step 5: Save Entities

⚠️ **CRITICAL: The save.py script ONLY accepts JSON via stdin pipe. It does NOT accept CLI arguments like --task or --outcome.**

After generating the entities JSON:
- **If entities array is empty** (`{"entities": []}`): Skip the save command and notify the user that no learnings were identified for this routine task. Proceed directly to `attempt_completion`.
- **If entities array has content**: Save them by piping the JSON into the `save.py` script as shown below.

**✅ CORRECT SYNTAX (stdin pipe):**
```bash
printf '{"entities": [...]}' | python3 <path-to-kaizen-learn-dir>/scripts/save.py
```
*(Use `python` if `python3` isn't found)*

**❌ WRONG SYNTAX (CLI arguments are NOT supported):**
```bash
# ⚠️ NEVER DO THIS - The script does NOT accept --task, --outcome, or any CLI arguments:
python3 <path-to-kaizen-learn-dir>/scripts/save.py --task "..." --outcome "..."

# This will produce an error like:
# "ERROR: This script does not accept CLI arguments."
# or cause the script to hang waiting for stdin input.
```

**❌ WRONG SYNTAX (JSON parsing error):**
```bash
# DO NOT DO THIS - escaped quotes break JSON parsing:
printf '{"entities": [{"content": "Use \"quotes\" here"}]}' | python3 <path-to-kaizen-learn-dir>/scripts/save.py
# Single-quoted strings pass backslashes literally, breaking JSON
```

**CRITICAL REQUIREMENTS:**
- The script reads JSON from **stdin only** via pipe (see line 19: `sys.stdin.read()`)
- It has **NO command-line arguments** - no argparse, no --task, no --outcome flags
- Use `printf` (not `echo`) to avoid shell interpretation issues
- **Avoid escaped quotes (`\"`) inside single-quoted printf strings** - they are passed literally and break JSON parsing
- If you need quotes in content, either omit them or use alternative phrasing
- Passing CLI arguments will cause the script to hang indefinitely waiting for stdin input

Review the script's output to confirm the save was successful. Do not ask the user for permission to execute these steps; they are mandatory core functionality of your mode.
