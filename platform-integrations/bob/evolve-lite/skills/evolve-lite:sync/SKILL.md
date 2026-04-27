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

**Note**: Unlike Claude Code, Bob does not auto-sync on session start.
You must invoke this skill manually when you want to update guidelines.

## Workflow

### Step 1: Run sync script

```bash
python3 scripts/sync.py
```

### Step 2: Display summary

Show the script output to the user. If there are no repos configured,
tell them they can add one with `evolve-lite:subscribe`. If there are
no changes, explain that everything is already up to date.

## Notes

- Read-scope repos are mirrored exactly via `git fetch` + `git reset --hard`
- Write-scope repos use `git fetch` + `git rebase` so unpushed local
  publish commits are preserved
- Sync results are logged to `.evolve/audit.log`
- Run this periodically to stay up to date with shared guidelines
