---
name: learn
description: Must be used near the end of any non-trivial turn that produced potentially reusable tools, guidance, errors, workarounds, or workflows, so those lessons are saved for future turns.
context: fork
---

# Entity Generator

## Overview

This skill analyzes the current conversation to extract actionable instructions that would help on similar tasks in the future. It **identifies errors encountered during the conversation** - tool failures, exceptions, wrong approaches, retry loops - and provides recommendations to prevent those errors from recurring. This skill should take note of the concrete solution which solved a concrete problem, not an abstract idea. When the successful resolution involves a non-trivial workaround, parser, command sequence, or fallback pipeline that could be used to avoid wasted effort, capture that solution as a reusable artifact first, then save entities that point future agents to use it.

## When To Use

Use this skill after completing meaningful work in the turn, especially when encountering:
- tool failures
- permission issues
- missing dependencies
- retries or abandoned approaches
- reusable command sequences or scripts

Examples of artifacts that must be immediately created once proven as the successful solution include:
- an inline Python, shell, or other heredoc script
- a command assembled interactively over multiple retries
- a parser or extractor implemented ad hoc during the turn
- a fallback path triggered by missing dependencies or restricted tooling

Unless that artifact happens to be:
- code which is a trivial one-liner that future agents would not benefit from reusing
- code which embeds secrets, tokens, or user-specific sensitive data
- a guideline that would instruct the agent to invoke a skill, tool, or external command by name (e.g. "run /evolve-lite:learn", "call save_trajectory") - such guidelines trigger prompt-injection detection when retrieved by the recall skill in a future session
- the user explicitly asked for a one-off result and not to persist helper code
- redundant because an equivalent local artifact on disk would be just as effective

## Workflow

### Step 0: Load the Conversation

This skill runs in a forked context. **You cannot see the parent conversation directly** — the only way to access it is by reading the trajectory file the save-trajectory stop hook just wrote to disk. Do not infer from your own (empty) conversation that there's nothing to learn; the parent's real work is in that file.

The stop-hook message (produced by `on_stop.py`) contains the literal marker `The saved trajectory path is: <path>` — a copy of the session transcript saved inside the project tree at `.evolve/trajectories/claude-transcript_<session-id>.jsonl`. Take everything after the colon, strip surrounding whitespace and quotes, and use the result as `saved_trajectory_path`. You will also attach this exact path to each entity's `trajectory` field in Step 6.

**Read this file with the `Read` tool — do NOT shell out.** `Read` pages large files natively (use its `offset` / `limit` parameters if needed). Do not use `cat`, `head`, `wc`, `find`, or `python3 -c` loops on the transcript — those trigger a permission prompt for every invocation and are unnecessary.

If the saved trajectory file does not exist (e.g., the save-trajectory hook did not run, or no marker was provided), output zero entities and exit. Do NOT fall back to reading the live session transcript under `~/.claude/projects/` — that path is outside the project tree, triggers permission prompts, and may be larger than the fork can consume.

The transcript is JSONL: each line is a separate JSON object. Filter for `"type": "assistant"` and `"type": "human"` lines, then reconstruct the flow from `message.content`. Look for tool calls, errors in tool results, and user corrections.

### Step 1: Analyze the Conversation

Identify from the saved trajectory loaded in Step 0:

- **Task/Request**: What was the user asking for?
- **Steps Taken**: What reasoning, actions, and observations occurred?
- **What Worked**: Which approaches succeeded?
- **What Failed**: Which approaches did not work and why?
- **Errors Encountered**: Tool failures, exceptions, permission errors, retry loops, dead ends, and wrong initial approaches
- **Reusable Outcome**: Did the final working solution produce a reusable script, parser, command template, or workflow that would save time on a similar task?

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
| 1 | `jq: command not found` | System tool unavailable in environment | created a python script to resolve the problem | Save the python script and use it in similar scenarios |
| 2 | `git push` rejected (no upstream) | Branch not tracked to remote | Added `-u origin branch` | Always set upstream when pushing a new branch |
| 3 | Tried regex parsing of HTML, got wrong results | Regex cannot handle nested tags | Switched to BeautifulSoup | Use a proper HTML parser, never regex |

### Step 3: Decide Whether To Save The Pipeline

Before writing entities, determine whether the successful approach should be saved as a reusable artifact.

Create or update a local reusable artifact when any of these are true:
- the final solution required more than a trivial one-liner
- the final solution worked around missing tools, libraries, or permissions
- the solution is likely to recur on similar tasks

Prefer one of these artifact forms:
- a small script, saved to a stable path in the workspace or plugin, such as `scripts/`, `tools/`, or another obvious helper location.
- a documented local workflow if code is not appropriate

When turning an ad hoc command or script into a reusable artifact, remove
incidental one-off inputs such as literal file names, IDs, answer values, or
temporary paths. Keep the reusable procedure that was actually exercised in the
session, and do not add capabilities that were not validated by the work.

If you create an artifact, record:
- its path
- what it does
- when future agents should use it first

### Step 4: Review Existing Guidelines

Before extracting, look at what has already been saved for this project. Earlier Stop hooks in the same session (or prior sessions) may have recorded guidelines that cover the same ground — re-extracting them is wasteful and pollutes the library.

Use the **Glob tool** to enumerate existing guideline files: `.evolve/entities/**/*.md`. Then use the **Read tool** to open each match and skim the content + trigger.

**Do NOT use `cat`, `head`, `find`, a `for` loop, or an inline `python3 -c` script for this.** Each shell invocation triggers a permission prompt, and Glob + Read cover the same need without any prompting.

If there are no existing guidelines, skip this step.

With the existing-guideline set in mind, when you proceed to Step 5 you should pick only *complementary* findings — new angles, new failure modes, or finer-grained detail — and drop candidates that restate or near-duplicate anything already saved. (`save_entities.py` will also drop exact-match duplicates at write time, but it cannot catch re-wordings.)

### Step 5: Extract Entities

If Step 3 produced an artifact, at least one entity must explicitly point to that artifact, which is likely the only entity that needs to be produced.
Otherwise, extract 3-5 proactive entities. Prioritize entities derived from errors identified in Step 2.

Follow these principles:

1. **Reframe failures as proactive recommendations**
    - If an approach failed due to permissions, recommend the working permission-aware approach first
    - If a system tool was unavailable, recommend the saved artifact or fallback workflow first
    - If an approach hit environment constraints, recommend the constraint-aware approach

2. **Prioritize known working local artifacts over general advice**
    - If the successful solution produced or reused a concrete local artifact, at least one saved entity must:
    - Bad: "Use Python to parse EXIF if exiftool is missing"
    - Better: "Use `/abs/path/json_get.py` for JSON field extraction when `jq` is unavailable in minimal environments."
    - name the artifact by path
    - state exactly when to use it
    - state that it should be tried before generic tool discovery or fallback exploration
    - describe the artifact by capability, not just by the original incident

3. **Triggers should describe the broad task context that the artifact solves, not the narrow details of the original request.**
    - Bad trigger: "When jq fails"
    - Good trigger: "When extracting fields from JSON in constrained shells or stripped-down environments"
    The trigger should generalize the working solution without becoming vague.

4. **For retry loops, recommend the final working approach as the starting point**
    - Eliminate trial and error by creating a concrete local artifact out of the successful workflow or script

5. **Prefer entities that save future time**
    - A pointer to a saved working script is more valuable than a generic reminder if both are available

### Step 6: Output Entities JSON

Output entities in this JSON format. Include a `trajectory` field on every entity, set to the `saved_trajectory_path` extracted in Step 0 — this records which session produced the guideline.

```json
{
  "entities": [
    {
      "content": "Proactive entity stating what TO DO",
      "rationale": "Why this approach works better",
      "type": "guideline",
      "trigger": "Situational context when this applies",
      "trajectory": ".evolve/trajectories/claude-transcript_<session-id>.jsonl"
    }
  ]
}
```

Allowed type values:
- guideline
- workflow
- script
- command-template

### Step 7: Save Entities

After generating the entities JSON, save them using the helper script:

#### Method 1: Direct Pipe (Recommended)

```bash
echo '<your-json-output>' | python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/learn/scripts/save_entities.py
```

#### Method 2: From File

```bash
cat entities.json | python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/learn/scripts/save_entities.py
```

#### Method 3: Interactive

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/learn/scripts/save_entities.py
```

The script will:
- Find or create the entities directory at `.evolve/entities/`
- Write each entity as a markdown file in `{type}/` subdirectories
- Deduplicate against existing entities
- Display confirmation with the total count

## Best Practices
1. Prioritize error-derived entities first.
2. One distinct error should normally produce one prevention entity.
3. Keep entities specific and actionable.
4. Include rationale so the future agent understands why the guidance matters.
5. Use situational triggers instead of failure-based triggers.
6. Limit output to the 3-5 most valuable entities.
7. If more than five distinct errors appear, merge entities with the same root cause or fix, then rank the rest by severity, frequency, user impact, and recency before dropping the weakest ones.
