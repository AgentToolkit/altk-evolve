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
sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:sync/scripts/sync.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:sync/scripts/sync.py"; python3 "$script"'
```

### Step 2: Display summary

Display the script's stdout verbatim to the user. Example outputs:

> "Synced 2 repo(s): memory [write] (+2 added, 0 updated, 0 removed), bob [read] (+0 added, 1 updated, 0 removed)"
>
> "No subscriptions configured. Add one with /evolve-lite:subscribe to start syncing shared guidelines."

Under `--quiet`, the script exits silently when there's nothing to report.
