---
name: publish
description: Publish a private guideline to a configured write-scope repo.
---

# Publish a Guideline

## Overview

Publish one or more private guidelines from `.evolve/entities/guideline/`
into a configured **write-scope** repo. The entity is stamped with
`visibility: public`, `owner`, `published_at`, and `source`, moved into the
local clone of the write repo, and committed / pushed to the remote.

After publish, the same local clone is also what `/evolve-lite:sync` pulls
from — so you (and anyone else publishing to the same repo) stay in sync.

## Workflow

### Step 1: Bootstrap config if missing or incomplete

Check whether `evolve.config.yaml` exists in the project root.

**If it does not exist**, or has no write-scope repo configured, first ask:

> "You need at least one write-scope repo to publish to. Run /evolve-lite:subscribe with --scope write to set one up, then come back."

Then stop. (Do not silently create a config — the user must explicitly
choose the namespace they publish to.)

**If it exists** but `identity.user` is missing, ask:

> "What username would you like to use? (e.g. `alice`)"

Add it to the config.

Read `identity.user` from config to use as `{user}` when stamping ownership.

### Step 2: First-time setup

Ensure `.evolve/` is gitignored at the project root:

```bash
grep -qxF '.evolve/' .gitignore 2>/dev/null || echo '.evolve/' >> .gitignore
```

### Step 3: Pick the target write-scope repo

Read `repos:` from `evolve.config.yaml`. Filter to entries with
`scope: write`.

- **Zero entries** → tell the user to subscribe to a write-scope repo first, then stop.
- **Exactly one entry** → use it as the default (no prompt).
- **Multiple entries** → display them as a numbered list with their `notes`
  and ask which one to publish to.

Bind `{repo}` = the chosen entry's `name`, `{remote}` = its `remote`, and
`{branch}` = its `branch` (default `main`). These are referenced in
Steps 5–8 below.

### Step 4: List and select entities

List the files in `.evolve/entities/guideline/` (filenames only), display
them numbered, and ask:

> "Which guideline(s) would you like to publish to '{repo}'? Enter a number or comma-separated list of numbers."

Wait for the user's selection.

### Step 5: Ensure the local clone exists

The target clone lives at `.evolve/entities/subscribed/{repo}/`. If it is
not already a git repo, clone it now:

```bash
git clone --branch "{branch}" -- "{remote}" ".evolve/entities/subscribed/{repo}"
```

(This usually already exists because `/evolve-lite:subscribe` cloned it.)

### Step 6: Run publish script

For each selected entity file, run:

```bash
sh -lc 'real_home="$(python3 -c "import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)")"; config_home="${CLAW_CONFIG_HOME:-$real_home/.claw}"; script=".claw/skills/evolve-lite:publish/scripts/publish.py"; [ -f "$script" ] || script="$config_home/skills/evolve-lite:publish/scripts/publish.py"; python3 "$script" --entity "{filename}" --repo "{repo}" --user "{identity.user}"'
```

### Step 7: Commit and push

Build `{filenames_list}` as a comma-joined list of all selected filenames,
and `{guideline_paths}` as a space-joined list of the corresponding
`guideline/{filename}` paths inside the clone (these are the files the
publish script just wrote).

```bash
git -C ".evolve/entities/subscribed/{repo}" add -- {guideline_paths}
git -C ".evolve/entities/subscribed/{repo}" commit -m "[evolve] publish: {filenames_list}"
git -C ".evolve/entities/subscribed/{repo}" push origin "{branch}"
```

If `git push` succeeds, continue to Step 8.

### Step 7a: Recover from non-fast-forward rejection

If `git push` failed **and** its stderr contains `rejected`,
`non-fast-forward`, or `fetch first`, another writer pushed to
`{branch}` since your last sync. The local publish commit is intact —
rebase it onto the new remote tip and push once more:

```bash
git -C ".evolve/entities/subscribed/{repo}" fetch origin "{branch}"
git -C ".evolve/entities/subscribed/{repo}" rebase "origin/{branch}"
```

- **Rebase clean** → retry the push and continue to Step 8:

  ```bash
  git -C ".evolve/entities/subscribed/{repo}" push origin "{branch}"
  ```

- **Rebase conflicted** → attempt to resolve, then hand off to the
  user for review. Do **not** `git rebase --continue` or `git push`
  without an explicit user confirmation.

  1. List the conflicted files:

     ```bash
     git -C ".evolve/entities/subscribed/{repo}" status --porcelain
     ```

     Conflict codes: `UU` = both modified, `AA` = both added,
     `UD`/`DU` = delete/modify, `A` + binary = binary add. If **any**
     file is `UD`, `DU`, or binary, skip straight to the abort branch
     below — those are not safe to auto-resolve.

  2. For each `UU` / `AA` file, read it and produce a resolution:
     - The working tree contains `<<<<<<<`, `=======`, `>>>>>>>`
       markers. During a rebase, the section above `=======` (labeled
       `HEAD`) is the **remote's** version and the section below
       (labeled with the publish commit's sha) is **the publish
       change being replayed** — i.e., "theirs" and "ours" are
       swapped relative to a regular merge.
     - Decide an intent-preserving merge: if the edits are
       independent (different sections), interleave them. If they
       target the same paragraph, prefer keeping both distinct
       guideline bodies (e.g. append one under a subheading) rather
       than picking a side silently.
     - Write the resolved content back to the file. Do **not**
       `git add` it yet.

  3. Show the user what you propose — per file, include a one-line
     merge strategy plus the diff against the remote:

     ```bash
     git -C ".evolve/entities/subscribed/{repo}" diff HEAD -- {file}
     ```

     Then ask:

     > "I've attempted to resolve {N} conflicted file(s): {list}.
     > Each proposed resolution is in
     > `.evolve/entities/subscribed/{repo}/`. Review them and say
     > **continue** to finish the rebase and push, or **abort** to
     > roll back and resolve by hand."

  4. **User says continue** → stage the resolved files, finish the
     rebase, and push:

     ```bash
     git -C ".evolve/entities/subscribed/{repo}" add {resolved-files}
     git -C ".evolve/entities/subscribed/{repo}" rebase --continue
     git -C ".evolve/entities/subscribed/{repo}" push origin "{branch}"
     ```

     Then continue to Step 8. If `rebase --continue` surfaces a
     **new** conflict (unusual for publish since there's normally one
     commit), loop back to step 1 of this block.

  5. **User says abort**, or the conflict isn't safely resolvable
     (binary / delete-modify), or you lack confidence in the merge:

     ```bash
     git -C ".evolve/entities/subscribed/{repo}" rebase --abort
     ```

     After the abort the local publish commit is preserved at
     `.evolve/entities/subscribed/{repo}` but is not on the remote.
     Tell the user:

     > "Rolled back. Your commit for {filenames_list} is preserved
     > locally — nothing was lost, but it's not on the remote yet.
     > To finish publishing, either:
     >
     > 1. Resolve by hand:
     >    - `cd .evolve/entities/subscribed/{repo}`
     >    - `git fetch origin {branch} && git rebase origin/{branch}`
     >    - edit the conflicted files, `git add` them, `git rebase --continue`
     >    - `git push origin {branch}`
     > 2. Or, if the conflict is on a shared filename, re-run
     >    `/evolve-lite:publish` for a different name."

If `git push` failed for any **other** reason (auth, network, missing
remote ref), surface git's error to the user as-is and stop — a rebase
will not help.

### Step 8: Confirm

Tell the user:

> "Published {filenames_list} to repo '{repo}' ({remote})."
