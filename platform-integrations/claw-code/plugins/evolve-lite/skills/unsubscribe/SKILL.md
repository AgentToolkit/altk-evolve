---
name: unsubscribe
description: Remove a repo from the unified repos list and delete its local clone.
---

# Remove a Repo

## Overview

Remove a configured repo (any scope) from `evolve.config.yaml` and delete
its local clone. Warn the user before removing a **write-scope** repo since
any locally published entities that haven't been pushed will be lost.

## Workflow

### Step 1: List repos

Run the following and display the output as a numbered list. Include each
entry's `scope` and `notes`:

```bash
sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:unsubscribe/scripts/unsubscribe.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:unsubscribe/scripts/unsubscribe.py"; python3 "$script" --list'
```

### Step 2: Pick one

Ask the user:

> "Which repo would you like to remove? Enter the number."

### Step 3: Confirm (extra warning if write-scope)

If the chosen entry has `scope: write`, warn:

> "'{name}' is a write-scope repo. Removing it will delete the local clone AND any locally published entities that have not yet been pushed. Continue? (y/n)"

Otherwise:

> "This will remove '{name}' and delete `.evolve/entities/subscribed/{name}/`. Continue? (y/n)"

If the user answers anything other than `y` or `yes`, stop and tell them
the operation was cancelled.

### Step 4: Run unsubscribe script

For a **read-scope** repo, run:

```bash
sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:unsubscribe/scripts/unsubscribe.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:unsubscribe/scripts/unsubscribe.py"; python3 "$script" --name {name}'
```

For a **write-scope** repo (only after the user confirms in Step 3), add
`--force`. The script refuses to remove a write-scope repo without it,
since the local clone may hold unpushed publishes:

```bash
sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:unsubscribe/scripts/unsubscribe.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:unsubscribe/scripts/unsubscribe.py"; python3 "$script" --name {name} --force'
```

### Step 5: Confirm

Tell the user:

> "Removed '{name}'."
