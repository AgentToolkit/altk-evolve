---
name: retention
description: Apply data-retention rules to the local evolve store — flag or delete stale and unused memories and expired sessions (dry-run by default)
---

# Retention

## Overview

Runs the data-retention rules configured in `evolve.config.yaml` against the
local `.evolve/` store: private entities under `.evolve/entities/` and session
transcripts under `.evolve/trajectories/`. Rules match by entity type plus age
(`max_age_days`, from file mtime) or disuse (`max_unused_days`, from `recall`
rows in `.evolve/audit.log`), and either **flag** (a non-destructive frontmatter
marker) or **delete**. A `delete` rule on trajectories with
`cascade_derived: true` also deletes the entities derived from those sessions
(linked by their `trajectory:` frontmatter).

The script is **dry-run by default** — it never mutates anything unless
`--apply` is passed.

## Workflow

### Step 1: Require rules

Read `evolve.config.yaml`. If there is no `retention:` block with a `rules:`
list, show the user this example and stop:

```yaml
retention:
  rules:
    - name: stale-guidelines
      entity_type: guideline
      max_age_days: 90
      action: flag
    - name: old-sessions
      entity_type: trajectory
      max_age_days: 365
      action: delete
      cascade_derived: true
```

Each rule needs a `name` and at least one of `max_age_days` /
`max_unused_days`; `action` is `flag` (default) or `delete`. Rules are checked
top-to-bottom and the first match wins, so put narrow rules first.

### Step 2: Dry run

From the project root:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/retention/scripts/run_retention.py
```

Show the user the full report — every entry says what *would* be flagged or
deleted, why (`age`, `unused`, or `cascade:<session>`), by which rule, and on
what evidence. Relay any `WARNING` lines too: they say when a signal was
weaker than it looks (for example, disuse measured from file mtime because the
entity has no recall row).

### Step 3: Apply — only on explicit user confirmation

Deleting is destructive and there is no undo. Ask the user to confirm the
dry-run report first. Never pass `--apply` without an explicit go-ahead.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/retention/scripts/run_retention.py --apply
```

Relay the applied report back to the user.

## Notes

- **Flag** upserts `retention_flagged_at`, `retention_reason`, and
  `retention_rule` into the entity's frontmatter; the file's mtime is
  preserved so its age clock doesn't reset. Trajectory files are opaque JSON,
  so their flag is recorded in `.evolve/audit.log` only.
- Every applied action is logged to `.evolve/audit.log` as an
  `event: "retention"` row.
- Subscribed entities (`.evolve/entities/subscribed/`) are out of scope — they
  are git clones owned by the sync skill and local deletes would be restored
  on the next sync.
- A standalone policy file can be passed with `--policy <file>` (a `rules:`
  list in JSON or YAML), overriding the config block.
- Signal caveats, worth stating when you relay a report: age is **file mtime**
  (editing an entity resets its clock — there is no `created_at` in the
  store), and the disuse signal only exists for entities the agent recorded
  via the recall audit step. The `trajectory:` cascade link is supported but
  nothing writes it automatically today, so `cascade_derived` only fires for
  entities where that key was set by hand.
