---
name: sync
description: Pull the latest guidelines from all subscribed repos.
---

# Sync Subscriptions

## Overview

This skill pulls the latest guidelines from all repos you have subscribed to, keeping your local copies up to date.

**Note**: Unlike Claude Code, Bob does not auto-sync on session start. You must manually invoke this skill when you want to update subscribed guidelines.

## Workflow

### Step 1: Run sync script

```bash
python3 scripts/sync.py
```

### Step 2: Display summary

Display the summary output from the script to the user. For example:

> "Synced 2 repo(s): alice (+2 added, 0 updated, 0 removed), bob (+0 added, 1 updated, 0 removed)"

If there is nothing to report (no subscriptions or no changes), confirm:

> "All subscriptions are up to date."

## Notes

- This skill must be invoked manually (no auto-sync on session start)
- Pulls latest changes from all subscribed repos
- Mirrors entities to `.evolve/entities/subscribed/{name}/` for recall
- Updates are logged to `.evolve/audit.log`
- Run this periodically to stay up to date with shared guidelines