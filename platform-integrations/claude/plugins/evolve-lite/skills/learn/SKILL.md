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

This skill runs in a forked context with no access to the parent conversation. The stop hook message (produced by `on_stop.py`) contains two literal markers:

- `The session transcript is at: <path>` — the live session transcript. Take everything between that marker and the next marker (or end of message), strip whitespace and quotes, and use the result as `transcript_path`.
- `The saved trajectory path is: <path>` — the relative path of the saved trajectory copy under `.evolve/trajectories/`. Extract this the same way and remember it as `saved_trajectory_path` — you will attach it to each entity in Step 3.

Then read the session transcript:

```bash
cat <transcript_path>
```

**You must read this file to analyze the conversation** — the forked context has no other access to it.

The transcript is JSONL: each line is a separate JSON object. Focus on lines where `"type": "assistant"` or `"type": "human"` to reconstruct the conversation flow. Look for tool calls, errors in tool results, and user corrections.

If no transcript path was provided, fall back to `.evolve/trajectories/`, which may contain either format:

- **`trajectory_*.json`** — a single JSON object with `messages: [{role, content}, …]`. Prefer the most recent one; parse with `json.load`.
- **`claude-transcript_*.jsonl`** — raw Claude JSONL (same format as the primary `transcript_path`). Parse line-by-line.

If no transcript is available at all, output zero entities.

### Step 1: Analyze the Conversation

Review the conversation (loaded from the transcript) and identify:

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

### Step 4: Assess Influence of Recalled Entities

Regardless of whether Step 3 produced new entities, judge whether the guidelines the recall hook served to *this* session were actually followed, contradicted, or simply irrelevant. This closes the provenance loop: the recall hook records *what* was served; this step records *what effect* it had.

1. Derive this session's `session_id` — it's `Path(transcript_path).stem` (same `transcript_path` extracted in Step 0).

2. Read `.evolve/audit.log` (JSONL, one object per line). Find every line where `event == "recall"` and `session_id` matches. Take the union of their `entities` arrays — that is the set of guideline slugs served to this session. If that set is empty, skip this step.

3. For each slug, locate its markdown file by searching under `.evolve/entities/` — the file may live at `.evolve/entities/guideline/<slug>.md` (local entities) or at `.evolve/entities/subscribed/<source>/guideline/<slug>.md` (entities recalled from a subscribed repository). Use a recursive search such as `find .evolve/entities -type f -name "<slug>.md"` and open the first match. Read its content + trigger — that is the guideline's intent. Skip the slug (log it as an assessment-less entry) if no file is found.

4. Compare against the transcript loaded in Step 0. For each slug, pick one verdict:
   - `followed` — the agent's actual actions are consistent with the guideline's recommendation.
   - `contradicted` — the guideline's trigger matched the task but the agent did the opposite, or hit the dead end the guideline would have prevented.
   - `not_applicable` — the guideline's trigger didn't match what this session was about.

   Keep `evidence` to one short sentence citing a specific action or tool call from the transcript.

5. Emit one JSON payload and pipe it to the helper:

```bash
echo '{
  "session_id": "<session-id>",
  "assessments": [
    {"entity": "<slug>", "verdict": "followed", "evidence": "Agent imported struct and parsed APP1 directly"}
  ]
}' | python3 ${CLAUDE_PLUGIN_ROOT}/skills/learn/scripts/log_influence.py
```

Emit zero assessments (empty `assessments` list) when no recall events exist for this session.

## Quality Gate

Before saving, review each entity against this checklist:

- [ ] Does it fall into one of the three categories (shortcut, error prevention, user correction)?
- [ ] Would knowing this guideline beforehand have changed the agent's behavior in a concrete way?
- [ ] Is it specific enough that another agent could act on it without further context?
- [ ] Does it avoid instructing the agent to invoke a named skill or tool?

If any answer is no, drop the entity. **Zero entities is a valid output.**
