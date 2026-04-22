---
name: subscribe
description: Subscribe to another user's public guidelines repo.
---

# Subscribe to Guidelines

## Overview

This skill subscribes to another user's public guidelines repository. Their guidelines are cloned locally and become available in recall after sync.

## Workflow

### Step 1: Bootstrap config if missing

Check whether `evolve.config.yaml` exists in the project root.

If it does not exist, ask the user for a username and create:

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

Ask the user for:

- the remote URL for the guidelines repo
- a short local name such as `alice`

### Step 3: Run subscribe script

```bash
python3 plugins/evolve-lite/skills/subscribe/scripts/subscribe.py \
  --name "{name}" \
  --remote "{remote}" \
  --branch main
```

### Step 4: Confirm

Tell the user the subscription was added and they can run `evolve-lite:sync` immediately if they want to pull updates now.
