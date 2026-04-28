---
name: unsubscribe
description: Remove a subscription and delete the locally synced guidelines.
---

# Unsubscribe from Guidelines

## Overview

This skill removes a subscription and deletes both the local clone and mirrored recall entities for that subscription.

## Workflow

### Step 1: List subscriptions

Run:

```bash
python3 plugins/evolve-lite/skills/unsubscribe/scripts/unsubscribe.py --list
```

Show the subscriptions to the user and ask which one to remove.

### Step 2: Confirm

Confirm that removing the subscription will delete:

- `.evolve/subscribed/{name}/`
- `.evolve/entities/subscribed/{name}/`

### Step 3: Run unsubscribe script

```bash
python3 plugins/evolve-lite/skills/unsubscribe/scripts/unsubscribe.py --name "{name}"
```

### Step 4: Confirm

Tell the user the subscription was removed.
