---
name: publish
description: Publish a private guideline to your public repo so others can subscribe to it.
---

# Publish Guideline

## Overview

This skill publishes one or more private guidelines from your local `.evolve/entities/guideline/` directory to your public git repository, making them available for others to subscribe to.

## Workflow

### Step 1: Bootstrap config if missing or incomplete

Check whether `evolve.config.yaml` exists in the project root.

**If it does not exist**, ask the user:

> "No `evolve.config.yaml` found. What username would you like to use? (e.g. `vatche`)"
> "What is the remote URL for your public guidelines repo? (e.g. `git@github.com:vatche/evolve-guidelines.git`)"

Create `evolve.config.yaml`:

```yaml
identity:
  user: {username}
public_repo:
  remote: {remote}
  branch: main
subscriptions: []
sync:
  on_session_start: true
```

**If it exists** but `identity.user` is missing, ask for it and add it to the config.

**If it exists** but `public_repo.remote` is missing, ask:

> "What is the remote URL for your public guidelines repo? (e.g. `git@github.com:vatche/evolve-guidelines.git`)"

Add it to the config.

Read `identity.user` from config to use as `{user}` when stamping ownership.

### Step 2: First-time setup

Ensure `.evolve/` is gitignored at the project root:

```bash
grep -qxF '.evolve/' .gitignore 2>/dev/null || echo '.evolve/' >> .gitignore
```

If `.evolve/public/` does not already contain a `.git` directory, initialise it and add the remote:

```bash
git init .evolve/public
git -C .evolve/public remote add origin {public_repo.remote}
```

### Step 3: List and select entities

List the files in `.evolve/entities/guideline/` (filenames only) and display them numbered. Ask the user:

> "Which guideline(s) would you like to publish? Enter a number or comma-separated list of numbers."

Wait for the user's selection.

### Step 4: Run publish script

For each selected entity file, run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/publish/scripts/publish.py \
  --entity "{filename}" \
  --user "{identity.user}"
```

### Step 5: Commit and push

Build `{filenames_list}` as a comma-joined list of all selected filenames (e.g. `foo.md, bar.md`).

```bash
git -C .evolve/public add .
git -C .evolve/public commit -m "[evolve] publish: {filenames_list}"
git -C .evolve/public push origin "{public_repo.branch}"
```

Where `{public_repo.branch}` defaults to `main` if not set in config.

### Step 6: Confirm

Tell the user:

> "Published {filenames_list} to {public_repo.remote}."
