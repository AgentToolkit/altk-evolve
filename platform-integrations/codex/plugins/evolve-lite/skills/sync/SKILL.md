---
name: sync
description: Pull the latest guidelines from every configured repo (read- and write-scope).
---

# Sync Repos

## Overview

Pull the latest guidelines from every repo in `evolve.config.yaml`
`repos:` list — both `scope: read` (subscribe-only) and `scope: write`
(publish targets). Write-scope repos use a rebase strategy so any
unpushed local publish commits are preserved.

## Workflow

### Step 1: Run sync script

```bash
python3 plugins/evolve-lite/skills/sync/scripts/sync.py
```

### Step 2: Display summary

Show the script output to the user. If there are no repos configured,
tell them they can add one with `evolve-lite:subscribe`. If there are no
changes, explain that everything is already up to date.
