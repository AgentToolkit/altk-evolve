---
name: sync
description: Pull the latest guidelines from all subscribed repos.
---

# Sync Subscriptions

## Overview

This skill pulls the latest guidelines from all subscribed repos and mirrors them into local recall storage.

## Workflow

### Step 1: Run sync script

```bash
python3 plugins/evolve-lite/skills/sync/scripts/sync.py
```

### Step 2: Display summary

Show the script output to the user. If there are no subscriptions, tell them they can add one with `evolve-lite:subscribe`. If there are no changes, explain that everything is already up to date.
