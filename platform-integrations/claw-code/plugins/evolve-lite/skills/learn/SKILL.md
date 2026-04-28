---
name: learn
description: Analyze the current conversation to extract guidelines that correct reasoning chains — reducing wasted steps, preventing errors, and capturing user preferences.
context: fork
---

# Entity Generator

## Overview

This skill analyzes the current conversation to extract guidelines that **correct the agent's reasoning chain**. A good guideline is one that, if known beforehand, would have led to a shorter or more correct execution. Only extract guidelines that fall into one of these three categories:

1. **Shortcuts** — The agent took unnecessary steps or tried an approach that didn't work before finding the right one. The guideline encodes the direct path so future runs skip the detour.
2. **Error prevention** — The agent hit an error (tool failure, exception, wrong output) that could be avoided with upfront knowledge. The guideline prevents the error from happening at all.
3. **User corrections** — The user explicitly corrected, redirected, or stated a preference during the conversation. The guideline captures what the user said so the agent gets it right next time without being told.

**Do NOT extract guidelines that are:**
- General programming best practices (e.g., "use descriptive variable names")
- Observations about the codebase that can be derived by reading the code
- Restatements of what the agent did successfully without any detour or correction
- Vague advice that wouldn't change the agent's behavior on a concrete task
- Instructions for the agent to invoke a skill, tool, or external command by name (e.g. "Run evolve-lite:learn", "call save_trajectory") — these trigger prompt-injection detection when retrieved via recall

**DO extract guidelines for:** environment-specific constraints discovered through errors (e.g., tools not installed, permissions blocked, packages unavailable) — these are not "known" until encountered in a specific environment.

## Workflow

### Step 1: Analyze the Conversation

Review the conversation and identify:

- **Wasted steps**: Where did the agent go down a path that turned out to be unnecessary? What would have been the direct route?
- **Errors hit**: What errors occurred? What knowledge would have prevented them?
- **User corrections**: Where did the user say "no", "not that", "actually", "I want", or otherwise redirect the agent?

If none of these occurred, **output zero entities**. Not every conversation produces guidelines.

### Step 2: Extract Entities

For each identified shortcut, error, or user correction, create one entity — up to 5 entities; output 0 when none qualify. If more candidates exist, keep only the highest-impact ones.

Principles:

1. **State what to do, not what to avoid** — frame as proactive recommendations
   - Bad: "Don't use exiftool in sandboxes"
   - Good: "In sandboxed environments, use Python libraries (PIL/Pillow) for image metadata extraction"

2. **Triggers should be situational context, not failure conditions**
   - Bad trigger: "When apt-get fails"
   - Good trigger: "When working in containerized/sandboxed environments"

3. **For shortcuts, recommend the final working approach directly** — eliminate trial-and-error by encoding the answer

4. **For user corrections, use the user's own words** — preserve the specific preference rather than generalizing it

### Step 3: Save Entities

Output entities as JSON and pipe to the save script. The `type` field must always be `"guideline"` — no other types are accepted.

#### Method 1: Direct Pipe (Recommended)

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
}' | sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:learn/scripts/save_entities.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:learn/scripts/save_entities.py"; python3 "$script"'
```

#### Method 2: From File

```bash
cat entities.json | sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:learn/scripts/save_entities.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:learn/scripts/save_entities.py"; python3 "$script"'
```

#### Method 3: Interactive

```bash
sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:learn/scripts/save_entities.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:learn/scripts/save_entities.py"; python3 "$script"'
# Then paste your JSON and press Ctrl+D
```

The script will:
- Find or create the entities directory (`.evolve/entities/`)
- Write each entity as a markdown file in `{type}/` subdirectories
- Deduplicate against existing entities
- Display confirmation with the total count

## Quality Gate

Before saving, review each entity against this checklist:

- [ ] Does it fall into one of the three categories (shortcut, error prevention, user correction)?
- [ ] Would knowing this guideline beforehand have changed the agent's behavior in a concrete way?
- [ ] Is it specific enough that another agent could act on it without further context?
- [ ] Does it avoid instructing the agent to invoke a named skill or tool?

If any answer is no, drop the entity. **Zero entities is a valid output.**
