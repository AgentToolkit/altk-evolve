---
name: provenance
description: Analyze saved trajectories and recall audit events offline to record whether recalled guidelines influenced completed sessions.
---

# Provenance Analyzer

## Overview

This skill runs after one or more sessions have completed. It reads `recall`
events from `.evolve/audit.log`, locates each session's trajectory, and records
post-hoc `influence` events for the recalled guidelines.

The mechanical work — reading recall rows, skipping already-assessed pairs,
resolving entity files, and locating trajectories — is done deterministically by
`provenance.py candidates`. Your job is the judgment: read each candidate and
decide whether the recalled guideline was `followed`, `contradicted`, or
`not_applicable`, then persist that verdict.

Use this skill when you want to compute usage provenance without coupling the
work to the live learn step.

## Workflow

### Step 1: Get candidates

Run the candidate builder. It emits one JSON object per line (JSONL), one per
unresolved `(session_id, entity)` recall pair:

```bash
sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:provenance/scripts/provenance.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:provenance/scripts/provenance.py"; python3 "$script" candidates'
```

Each candidate looks like:

```json
{
  "session_id": "<session-id>",
  "entity_id": "<type>/<name>",
  "entity_excerpt": "<frontmatter + content of the entity file>",
  "trajectory_path": "/path/to/transcript.jsonl",
  "trajectory_excerpt": "<head of the trajectory transcript>",
  "missing": ["trajectory"]
}
```

Notes:

- `entity_id` is the path relative to `.evolve/entities/` without the `.md`
  suffix, e.g. `feedback/foo`, `guideline/bar`, or
  `subscribed/alice/guideline/baz`.
- Pairs that already have an `influence` row are skipped for you — the builder
  reuses the same dedup rule used when influence rows are written. You will
  never be handed a duplicate.
- The trajectory locator checks `.evolve/trajectories/` first, then falls back
  to the native Claude transcript at
  `~/.claude/projects/<slug>/<session-id>.jsonl`. This means provenance works
  even when no `.evolve/trajectories/` file was written.
- If an entity file or trajectory cannot be found, the candidate is still
  emitted with a `missing: [...]` field so the gap is visible. When the
  trajectory is missing you usually cannot judge the pair — skip it (do not
  guess), unless the entity content alone makes `not_applicable` certain.

### Step 2: Judge each candidate

For each candidate, read `entity_excerpt` (and open `trajectory_path` for the
full transcript if the excerpt is not enough). Compare the recalled guideline
against the agent's actual actions in the trajectory and pick exactly one
verdict:

- `followed` — the agent's actual actions are consistent with the guideline.
- `contradicted` — the guideline applied, but the agent did the opposite or
  repeated the avoidable dead end.
- `not_applicable` — the guideline was recalled but did not apply to this
  session.

Keep `evidence` to one short sentence citing a concrete action, tool call, or
absence in the trajectory. This judgment is yours — there is no heuristic
fallback.

### Step 3: Record verdicts

Persist each verdict. Either pipe one verdict per call to `provenance.py
record`:

```bash
echo '{
  "session_id": "<session-id>",
  "entity": "<type>/<name>",
  "verdict": "followed",
  "evidence": "Agent used the saved parser before trying shell fallbacks."
}' | sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:provenance/scripts/provenance.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:provenance/scripts/provenance.py"; python3 "$script" record'
```

…or, to batch many assessments for one session in a single call, pipe to the
underlying writer directly:

```bash
echo '{
  "session_id": "<session-id>",
  "assessments": [
    {"entity": "feedback/foo", "verdict": "followed", "evidence": "Agent followed it."},
    {"entity": "guideline/bar", "verdict": "not_applicable", "evidence": "Did not apply."}
  ]
}' | sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:provenance/scripts/log_influence.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:provenance/scripts/log_influence.py"; python3 "$script"'
```

Both paths write the identical `influence` audit row and skip duplicates. The
`entity` value must match the candidate's `entity_id` exactly, including any
`subscribed/<source>/` prefix.

It is valid to record nothing when recall events exist but no recalled guideline
can be assessed (e.g. every candidate is missing its trajectory).
