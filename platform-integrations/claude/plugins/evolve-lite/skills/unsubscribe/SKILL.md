---
name: unsubscribe
description: Remove a subscription and delete the locally synced guidelines.
---

# Unsubscribe from Guidelines

## Overview

This skill removes a subscription and deletes the locally cloned guidelines for that subscription.

## Workflow

### Step 1: List subscriptions

Run the following and display the output as a numbered list:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/unsubscribe/scripts/unsubscribe.py --list
```

### Step 2: Pick one

Ask the user:

> "Which subscription would you like to remove? Enter the number."

### Step 3: Confirm

Ask the user:

> "This will remove '{name}' and delete `.evolve/subscribed/{name}/` and `.evolve/entities/subscribed/{name}/`. Continue? (y/n)"

If the user answers anything other than `y` or `yes`, stop and tell them the operation was cancelled.

### Step 4: Run unsubscribe script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/unsubscribe/scripts/unsubscribe.py --name {name}
```

### Step 5: Confirm

Tell the user:

> "Unsubscribed from {name}."
