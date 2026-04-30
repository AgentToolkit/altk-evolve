---
name: unsubscribe
description: Remove a repo from the unified repos list and delete its local clone.
---

# Remove a Repo

## Overview

Remove a configured repo (any scope) from `evolve.config.yaml` and delete
its local clone at `.evolve/entities/subscribed/{name}/`. Warn the user
before removing a write-scope repo since any unpushed local publish
commits will be lost.

## Workflow

### Step 1: List repos

Run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/unsubscribe/scripts/unsubscribe.py --list
```

Show the repos to the user (including `scope` and `notes`) and ask which
one to remove.

### Step 2: Confirm

Confirm deletion of `.evolve/entities/subscribed/{name}/`. If the repo has
`scope: write`, add a warning that unpushed local publish commits will be
lost.

### Step 3: Run unsubscribe script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/unsubscribe/scripts/unsubscribe.py --name {name}
```

For a write-scope repo, the script refuses to remove the local clone
without `--force` so unpushed publishes can't disappear by accident:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/unsubscribe/scripts/unsubscribe.py --name {name} --force
```

### Step 4: Confirm

Tell the user the repo was removed.

## Notes

- This removes the entry from `evolve.config.yaml` `repos:` list
- Deletes `.evolve/entities/subscribed/{name}/` (the local clone, also
  the recall mirror)
- The entities will no longer appear in recall
