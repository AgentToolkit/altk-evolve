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
  what you have already published (and anything others have pushed to the
  same repo).

This skill adds one entry to `repos:` and clones it locally.

## Workflow

### Step 1: Bootstrap config if missing

Check whether `evolve.config.yaml` exists in the project root.

If it does **not** exist, ask the user:

> "No `evolve.config.yaml` found. What username would you like to use? (e.g. `vatche`)"

Then create `evolve.config.yaml` with this minimal content:

```yaml
identity:
  user: {username}
repos: []
sync:
  on_session_start: true
```

Also ensure `.evolve/` is gitignored:

```bash
grep -qxF '.evolve/' .gitignore 2>/dev/null || echo '.evolve/' >> .gitignore
```

### Step 2: Gather details

Ask the user in this order:

> "What is the remote URL for the guidelines repo? (e.g. `git@github.com:alice/evolve-guidelines.git`)"
> "What short name would you like for this repo? (e.g. `alice`)"
> "Scope? `read` (download-only subscription) or `write` (you can also publish to it)."
> "Optional note describing this repo (press Enter to skip)."

### Step 3: Check for duplicates

Read `evolve.config.yaml` from the project root. If the name already exists
in `repos:`, tell the user:

> "A repo named '{name}' is already configured. Unsubscribe it first or choose a different name."

Then stop.

### Step 4: Run subscribe script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/subscribe/scripts/subscribe.py \
  --name "{name}" \
  --remote "{remote}" \
  --branch main \
  --scope "{scope}" \
  --notes "{notes}"
```

### Step 5: Confirm

Tell the user:

> "Added '{name}' (scope={scope}). Run /evolve-lite:sync to pull the latest guidelines."
