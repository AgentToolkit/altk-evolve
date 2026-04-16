---
name: subscribe
description: Subscribe to another user's public guidelines repo.
---

# Subscribe to Guidelines

## Overview

This skill subscribes to another user's public guidelines repository. Their guidelines will be cloned locally and made available in your recall context.

## Workflow

### Step 1: Bootstrap config if missing

Check whether `evolve.config.yaml` exists in the project root.

If it does **not** exist, ask the user:

> "No `evolve.config.yaml` found. What username would you like to use? (e.g. `vatche`)"

Then create `evolve.config.yaml` with this minimal content:

```yaml
identity:
  user: {username}
subscriptions: []
sync:
  on_session_start: true
```

Also ensure `.evolve/` is gitignored:

```bash
grep -qxF '.evolve/' .gitignore 2>/dev/null || echo '.evolve/' >> .gitignore
```

### Step 2: Gather details

Ask the user:

> "What is the remote URL for the guidelines repo? (e.g. `git@github.com:alice/evolve-guidelines.git`)"

Then ask:

> "What short name would you like for this subscription? (e.g. `alice`)"

### Step 3: Check for duplicates

Read `evolve.config.yaml` from the project root. If the name already exists in the `subscriptions` list, tell the user:

> "A subscription named '{name}' already exists. Please unsubscribe first or choose a different name."

Then stop.

### Step 4: Run subscribe script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/subscribe/scripts/subscribe.py \
  --name {name} \
  --remote {remote} \
  --branch main
```

### Step 5: Confirm

Tell the user:

> "Subscribed to {name}. Run /sync to pull their guidelines."
