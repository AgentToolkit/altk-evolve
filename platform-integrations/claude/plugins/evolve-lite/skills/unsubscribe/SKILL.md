---
name: unsubscribe
description: Remove a repo from the unified repos list and delete its local clone.
---

# Remove a Repo

## Overview

Remove a configured repo (any scope) from `evolve.config.yaml` and delete
its local clone. Warn the user before removing a **write-scope** repo since
any locally published entities that haven't been pushed will be lost.

## Workflow

### Step 1: List repos

Run the following and display the output as a numbered list. Include each
entry's `scope` and `notes`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/unsubscribe/scripts/unsubscribe.py --list
```

### Step 2: Pick one

Ask the user:

> "Which repo would you like to remove? Enter the number."

### Step 3: Confirm (extra warning if write-scope)

If the chosen entry has `scope: write`, warn:

> "'{name}' is a write-scope repo. Removing it will delete the local clone AND any locally published entities that have not yet been pushed. Continue? (y/n)"

Otherwise:

> "This will remove '{name}' and delete `.evolve/entities/subscribed/{name}/`. Continue? (y/n)"

If the user answers anything other than `y` or `yes`, stop and tell them
the operation was cancelled.

### Step 4: Run unsubscribe script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/unsubscribe/scripts/unsubscribe.py --name {name}
```

### Step 5: Confirm

Tell the user:

> "Removed '{name}'."
