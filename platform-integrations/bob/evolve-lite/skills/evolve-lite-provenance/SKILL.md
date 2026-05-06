---
name: evolve-lite:provenance
description: Analyze saved trajectories and recall audit events offline to record whether recalled guidelines influenced completed sessions.
---

# Provenance Analyzer

## Overview

This skill runs after one or more sessions have completed. It reads saved trajectories from `.evolve/trajectories/`, matches them to `recall` events in `.evolve/audit.log`, and records post-hoc `influence` events for recalled guidelines.

Use this skill when you want to compute usage provenance without coupling the work to the live learn step.

## Workflow

### Step 1: Load Recall Events

Read `.evolve/audit.log` as JSONL. Find entries where `event == "recall"` and `entities` is a non-empty list.

Skip any recall event that already has `influence` entries for the same `session_id` and entity ids. Do not write duplicate influence records.

### Step 2: Locate Saved Trajectories

List `.evolve/trajectories/` and match each recall event to a trajectory by `session_id`.

Supported trajectory names:
- `claude-transcript_<session-id>.jsonl`
- `trajectory_*.json` when its content corresponds to the session being assessed

If you cannot confidently match a recall event to a trajectory, skip it.

### Step 3: Read Recalled Entities

For each recalled entity id, open `.evolve/entities/<id>.md`. The id is a path relative to `.evolve/entities/` without the `.md` suffix, such as `guideline/foo` or `subscribed/alice/guideline/foo`.

Read the entity content and trigger. Skip ids whose files are missing.

### Step 4: Assess Influence

Compare each recalled entity with the matched trajectory. Pick exactly one verdict:

- `followed` - the agent's actual actions are consistent with the guideline.
- `contradicted` - the guideline applied, but the agent did the opposite or repeated the avoidable dead end.
- `not_applicable` - the guideline was recalled but did not apply to this session.

Keep `evidence` to one short sentence citing a concrete action, tool call, or absence in the trajectory.

### Step 5: Write Influence Events

Pipe one JSON payload per assessed session to the helper:

```bash
echo '{
  "session_id": "<session-id>",
  "assessments": [
    {"entity": "guideline/<slug>", "verdict": "followed", "evidence": "Agent used the saved parser before trying shell fallbacks."}
  ]
}' | python3 .bob/skills/evolve-lite-provenance/scripts/log_influence.py
```

The `entity` value must match exactly what appeared in the recall event, including any `subscribed/<source>/` prefix.

It is valid to emit an empty `assessments` list when recall events exist but no recalled guideline can be assessed.
