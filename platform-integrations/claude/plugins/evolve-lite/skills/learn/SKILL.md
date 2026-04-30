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

### Step 0: Load the Conversation

This skill runs in a forked context with no access to the parent conversation. The stop-hook message (produced by `on_stop.py`) contains one literal marker:

- `The saved trajectory path is: <path>` — a copy of the session transcript saved inside the project tree at `.evolve/trajectories/claude-transcript_<session-id>.jsonl`. Take everything after the colon, strip surrounding whitespace and quotes, and use the result as `saved_trajectory_path`. You will also attach this exact path to each entity's `trajectory` field in Step 4.

**Read this file with the `Read` tool — do NOT shell out.** `Read` pages large files natively (use its `offset` / `limit` parameters if needed). Do not use `cat`, `head`, `wc`, `find`, or `python3 -c` loops on the transcript — those trigger a permission prompt for every invocation and are unnecessary.

If the saved trajectory file does not exist (e.g., the save-trajectory hook did not run, or no marker was provided), output zero entities and exit. Do NOT fall back to reading the live session transcript under `~/.claude/projects/` — that path is outside the project tree, triggers permission prompts, and may be larger than the fork can consume.

The transcript is JSONL: each line is a separate JSON object. Focus on lines where `"type": "assistant"` or `"type": "human"` to reconstruct the conversation flow. Look for tool calls, errors in tool results, and user corrections.

### Step 1: Analyze the Conversation

Review the conversation (loaded from the transcript) and identify:

- **Wasted steps**: Where did the agent go down a path that turned out to be unnecessary? What would have been the direct route?
- **Errors hit**: What errors occurred? What knowledge would have prevented them?
- **User corrections**: Where did the user say "no", "not that", "actually", "I want", or otherwise redirect the agent?

If none of these occurred, **output zero entities**. Not every conversation produces guidelines.

### Step 2: Review Existing Guidelines

Before extracting, look at what has already been saved for this project. Earlier Stop hooks in the same session (or prior sessions) may have recorded guidelines that cover the same ground — re-extracting them is wasteful and pollutes the library.

Use the **Glob tool** to enumerate existing guideline files: `.evolve/entities/**/*.md`. Then use the **Read tool** to open each match and skim the content + trigger.

**Do NOT use `cat`, `head`, `find`, a `for` loop, or an inline `python3 -c` script for this.** Each shell invocation triggers a permission prompt, and Glob + Read cover the same need without any prompting.

If there are no existing guidelines, skip this step.

With the existing-guideline set in mind, when you proceed to Step 3 you should pick only *complementary* findings — new angles, new failure modes, or finer-grained detail — and drop candidates that restate or near-duplicate anything already saved. (`save_entities.py` will also drop exact-match duplicates at write time, but it cannot catch re-wordings.)

### Step 3: Extract Entities

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

### Step 4: Save Entities

Output entities as JSON and pipe to the save script. The `type` field must always be `"guideline"` — no other types are accepted. Include a `trajectory` field on every entity, set to the `saved_trajectory_path` extracted in Step 0 — this records which session produced the guideline.

#### Method 1: Direct Pipe (Recommended)

```bash
echo '{
  "entities": [
    {
      "content": "Proactive entity stating what TO DO",
      "rationale": "Why this approach works better",
      "type": "guideline",
      "trigger": "Situational context when this applies",
      "trajectory": ".evolve/trajectories/claude-transcript_<session-id>.jsonl"
    }
  ]
}' | python3 ${CLAUDE_PLUGIN_ROOT}/skills/learn/scripts/save_entities.py
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
