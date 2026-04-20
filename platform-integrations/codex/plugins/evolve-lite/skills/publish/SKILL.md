---
name: publish
description: Publish a private guideline to your public repo so others can subscribe to it.
---

# Publish Guideline

## Overview

This skill publishes one or more private guidelines from your local `.evolve/entities/guideline/` directory to your public git repository, moving them into the public store so others can subscribe to them.

## Workflow

### Step 1: Bootstrap config if missing or incomplete

Check whether `evolve.config.yaml` exists in the project root.

If it does not exist, ask the user for:

- a username such as `vatche`
- the remote URL for the public guidelines repo

Create `evolve.config.yaml` with:

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

If the file exists but `identity.user` or `public_repo.remote` is missing, ask only for the missing values and update the file.

### Step 2: First-time setup

Ensure `.evolve/` is gitignored at the project root:

```bash
grep -qxF '.evolve/' .gitignore 2>/dev/null || echo '.evolve/' >> .gitignore
```

If `.evolve/public/` does not already contain a `.git` directory, initialize it and add the configured remote:

```bash
git init .evolve/public
git -C .evolve/public remote add origin {public_repo.remote}
```

### Step 3: List and select entities

List the files in `.evolve/entities/guideline/` and ask the user which ones to publish.

### Step 4: Run publish script

For each selected file, run:

```bash
python3 plugins/evolve-lite/skills/publish/scripts/publish.py \
  --entity "{filename}" \
  --user "{identity.user}"
```

### Step 5: Commit and push

```bash
git -C .evolve/public add .
git -C .evolve/public commit -m "[evolve] publish: {name}"
git -C .evolve/public push origin "{public_repo.branch}"
```

### Step 6: Confirm

Tell the user what was published and where it was pushed.
