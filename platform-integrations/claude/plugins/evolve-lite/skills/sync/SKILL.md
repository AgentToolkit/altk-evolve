---
name: sync
description: Pull the latest guidelines from every configured repo (read- and write-scope).
---

# Sync Repos

## Overview

This skill pulls the latest guidelines from every repo in
`evolve.config.yaml` `repos:` list — both `scope: read` (subscribe-only)
and `scope: write` (publish targets). Write-scope repos use a rebase
strategy so any unpushed local publish commits are preserved.

## Workflow

### Step 1: Run sync script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync/scripts/sync.py
```

### Step 2: Display summary

Display the script's stdout verbatim to the user. Example outputs:

> "Synced 2 repo(s): memory [write] (+2 added, 0 updated, 0 removed), bob [read] (+0 added, 1 updated, 0 removed)"

> "No subscriptions configured. Add one with /evolve-lite:subscribe to start syncing shared guidelines."

Under `--quiet`, the script exits silently when there's nothing to report.
