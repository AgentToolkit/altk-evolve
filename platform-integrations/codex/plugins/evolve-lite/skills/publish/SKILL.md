---
name: publish
description: Publish a private guideline to a configured write-scope repo.
---

# Publish a Guideline

## Overview

Publish one or more private guidelines from `.evolve/entities/guideline/`
into a configured **write-scope** repo. The entity is stamped with
`visibility: public`, `owner`, `published_at`, and `source`, moved into
the local clone of the write repo, and committed / pushed to the remote.

The same local clone is also what `/evolve-lite:sync` pulls from — so you
and anyone else publishing to the same repo stay in sync.

## Workflow

### Step 1: Require a write-scope repo

Read `evolve.config.yaml`. If no entry has `scope: write`, tell the user:

> "You need at least one write-scope repo to publish to. Run evolve-lite:subscribe with --scope write to set one up, then come back."

Then stop.

If `identity.user` is missing, ask for it and add it to the config.

### Step 2: First-time setup

Ensure `.evolve/` is gitignored at the project root:

```bash
grep -qxF '.evolve/' .gitignore 2>/dev/null || echo '.evolve/' >> .gitignore
```

### Step 3: Pick the target write-scope repo

Filter `repos:` to entries with `scope: write` (Step 1 already aborted if
there were zero, so at least one exists here).

- Exactly one entry → use it as default.
- Multiple entries → show a numbered list with `notes` and ask which to publish to.

Let `{repo}` be the chosen repo name and `{branch}` its configured branch (default `main`).

### Step 4: List and select entities

List files in `.evolve/entities/guideline/` and ask the user which to publish.

### Step 5: Run publish script

For each selected file, run:

```bash
python3 plugins/evolve-lite/skills/publish/scripts/publish.py \
  --entity "{filename}" \
  --repo "{repo}" \
  --user "{identity.user}"
```

### Step 6: Commit and push

Build `{names}` as a comma-joined list of selected filenames, and
`{guideline_paths}` as a space-joined list of the corresponding
`guideline/{filename}` paths inside the clone (the files the publish
script just wrote).

```bash
git -C ".evolve/entities/subscribed/{repo}" add -- {guideline_paths}
git -C ".evolve/entities/subscribed/{repo}" commit -m "[evolve] publish: {names}"
git -C ".evolve/entities/subscribed/{repo}" push origin "{branch}"
```

On push success, continue to Step 7.

### Step 6a: Recover from non-fast-forward rejection

If the push fails and stderr mentions `rejected` / `non-fast-forward`
/ `fetch first`, another writer pushed to `{branch}` in between.
Rebase the local commit and push once more:

```bash
git -C ".evolve/entities/subscribed/{repo}" fetch origin "{branch}"
git -C ".evolve/entities/subscribed/{repo}" rebase "origin/{branch}"
```

- Rebase clean → retry `git push origin "{branch}"` once, then Step 7.
- Rebase conflicted → attempt to resolve, then hand off for user
  review. Do not `git rebase --continue` or `git push` without an
  explicit user confirmation.

  1. `git -C ".evolve/entities/subscribed/{repo}" status --porcelain`
     lists the conflicted paths. If any are `UD`, `DU`, or binary,
     skip to the abort step — those aren't safe to auto-resolve.
  2. For each `UU`/`AA` file, read the conflict markers. During a
     rebase, `<<<<<<< HEAD` is the **remote's** version and the
     section under the commit sha is the **publish change** being
     replayed (opposite of a regular merge). Write an
     intent-preserving resolution; don't `git add` yet.
  3. Show the user the diff (`git -C ".evolve/entities/subscribed/{repo}" diff HEAD -- {file}`) per
     resolved file with a one-line strategy summary, and ask whether
     to **continue** (stage + `rebase --continue` + push) or **abort**
     (roll back for manual resolution).
  4. On **continue**:

     ```bash
     git -C ".evolve/entities/subscribed/{repo}" add {resolved-files}
     git -C ".evolve/entities/subscribed/{repo}" rebase --continue
     git -C ".evolve/entities/subscribed/{repo}" push origin "{branch}"
     ```

     Then Step 7. If `rebase --continue` surfaces a new conflict, loop
     from step 1.
  5. On **abort** — user declined, conflict isn't safely resolvable,
     or the proposed merge feels unsafe:

     ```bash
     git -C ".evolve/entities/subscribed/{repo}" rebase --abort
     ```

     The local publish commit is preserved at
     `.evolve/entities/subscribed/{repo}` but not on the remote. Tell
     the user to either (a) resolve manually in that directory
     (`git fetch origin {branch} && git rebase origin/{branch}`, fix
     conflicts, `git add` + `git rebase --continue`, `git push origin
     {branch}`) or (b) re-run `evolve-lite:publish` with a different
     filename if the conflict is a shared name.

If the push fails for any other reason (auth, network, missing remote
ref), surface git's error and stop — rebase will not help.

### Step 7: Confirm

Tell the user what was published and to which repo.
