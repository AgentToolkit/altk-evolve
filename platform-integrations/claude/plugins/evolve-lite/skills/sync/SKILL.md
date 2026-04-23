---
name: sync
description: Pull the latest guidelines from all subscribed repos.
---

# Sync Subscriptions

## Overview

This skill pulls the latest guidelines from all repos you have subscribed to, keeping your local copies up to date.

## Workflow

### Step 1: Run sync script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync/scripts/sync.py
```

### Step 2: Display summary

Display the summary output from the script to the user. For example:

> "Synced 2 repo(s): alice (+2 added, 0 updated, 0 removed), bob (+0 added, 1 updated, 0 removed)"

If there is nothing to report (no subscriptions or no changes), confirm:

> "All subscriptions are up to date."
