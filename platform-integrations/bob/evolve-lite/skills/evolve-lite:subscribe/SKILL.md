---
name: subscribe
description: Add a shared guidelines repo (read-scope subscription or write-scope publish target) to the unified repos list.
---

# Subscribe to a Shared Repo

## Overview

Configured guidelines repos are multi-reader / multi-writer git databases,
described in a single unified list in `evolve.config.yaml`:

```yaml
repos:
  - name: memory
    scope: write
    remote: git@github.com:alice/evolve.git
    branch: main
    notes: public memory for foobar project
  - name: org-memory
    scope: read
    remote: git@github.com:acme/org-memory.git
    branch: main
    notes: private memory shared only within my org
```

- `scope: read` — download-only. Synced on every run.
- `scope: write` — publish target. Synced on every run too, so you see
  what you have already published and anything others have pushed.

## Workflow

### Step 1: Bootstrap config if missing

If `evolve.config.yaml` does not exist, ask the user for a username and
create:

```yaml
identity:
  user: {username}
repos: []
sync:
  on_session_start: false
```

Also ensure `.evolve/` is gitignored:

```bash
grep -qxF '.evolve/' .gitignore 2>/dev/null || echo '.evolve/' >> .gitignore
```

### Step 2: Gather details

Ask the user for:

- the remote URL for the guidelines repo
- a short local name such as `alice`
- the scope: `read` (default, subscribe-only) or `write` (also a publish target)
- an optional note

### Step 3: Run subscribe script

```bash
python3 scripts/subscribe.py \
  --name "{name}" \
  --remote "{remote}" \
  --branch main \
  --scope "{scope}" \
  --notes "{notes}"
```

### Step 4: Confirm

Tell the user the repo was added and they can run `evolve-lite:sync`
immediately if they want to pull updates now.

## Notes

- The repo is cloned directly into `.evolve/entities/subscribed/{name}/`,
  which doubles as the recall mirror
- Subscribed entities will appear in recall with `[from: {name}]`
  annotations
- Read-scope repos use a shallow clone; write-scope repos use a full
  clone so publish commits can be rebased and pushed cleanly
