# Evolve Lite for Bob

A Bob integration that gives the agent a persistent, shareable memory of your
project.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

## Features

Recall and save are driven by instructions, not skills. The installer writes
`EVOLVE.md` into Bob's global rules directory, where it is loaded into every
session; it tells the agent to read `./.evolve/entities/` as its first action on
a non-trivial task, and to write an entity near the end only when it learned
something durable.

The commands cover everything around that loop — `/evolve-lite-adapt-memory`,
`/evolve-lite-publish`, `/evolve-lite-subscribe`, `/evolve-lite-sync`,
`/evolve-lite-unsubscribe`, `/evolve-lite-provenance`, `/evolve-lite-retention`,
`/evolve-lite-save`, `/evolve-lite-save-trajectory`,
`/evolve-lite-synthesize-skill`.

> Bob resolves skills by folder name, so everything here uses the dash form
> (`evolve-lite-publish`), not the colon form other hosts use.

## Installation

Run the installation script from the repository root:

```bash
bash platform-integrations/install.sh install --platform bob --mode lite
```

This installs:
- 10 skills in `~/.bob/skills/` and a matching slash command per skill in `~/.bob/commands/`
- Shared library in `~/.bob/lib/evolve-lite/`
- The always-on instructions at `~/.bob/rules/00-evolve-lite.md` (Bob loads every
  `~/.bob/rules/*.md` into every session)
- The recall-audit script at `~/.bob/evolve-lite/audit_recall.py`, allowlisted in
  Bob's settings so it runs without a prompt

## How It Works

### Guideline Storage

Guidelines are stored as individual markdown files in `.evolve/entities/`,
organized by source. Both read-scope subscriptions and write-scope publish
targets live under `entities/subscribed/{name}/`:

```text
.evolve/entities/
  guideline/                            # Private guidelines
    use-context-managers.md
  subscribed/
    memory/                             # write-scope clone (publishes land here)
      guideline/
        my-published-guideline.md
    alice/                              # read-scope clone
      guideline/
        her-guideline.md
```

Each file uses markdown with YAML frontmatter:

```markdown
---
type: guideline
trigger: When processing files or managing resources
visibility: private
---

Use context managers for file operations

## Rationale

Context managers ensure proper resource cleanup
```

## Sharing Guidelines

Evolve Lite treats shared guidelines as multi-reader / multi-writer git
databases. A single unified `repos:` list in `evolve.config.yaml`
describes every external guideline repo; each entry has a `scope` of
`read` (subscribe only) or `write` (publish target that is also pulled
on sync).

### Setup

Sharing requires `evolve.config.yaml` at the project root. If it doesn't
exist, the subscribe or publish skills will prompt you to create one.
Minimal structure:

```yaml
identity:
  user: yourname          # used to stamp ownership on published guidelines

repos:
  - name: memory
    scope: write
    remote: git@github.com:yourname/evolve-memory.git
    branch: main
    notes: public memory for my open-source projects
  - name: team
    scope: read
    remote: git@github.com:myorg/evolve-guidelines.git
    branch: main

sync:
  on_session_start: false
```

The `.evolve/` directory is kept out of version control — the skills
automatically add it to `.gitignore`.

### Subscribing to a Repo

Use `evolve-lite-subscribe` to add either a read-scope subscription or a
write-scope publish target:

```text
evolve-lite-subscribe
> Remote URL: git@github.com:alice/evolve-guidelines.git
> Short name: alice
> Scope: read
```

The repo is cloned directly into `.evolve/entities/subscribed/{name}/`
(this directory serves as both the git clone and the recall mirror).

### Publishing Guidelines

Use `evolve-lite-publish` to share local guidelines via a **write-scope** repo:

1. The skill picks (or asks about) the write-scope target repo
2. Lists files in `.evolve/entities/guideline/`
3. You pick which ones to publish
4. Each selected file is moved into the write-scope clone at
   `.evolve/entities/subscribed/{repo}/guideline/`, stamped with your
   username, committed, and pushed to the remote

Because the publish target is also a subscribed repo, your next sync
pulls in anything other writers have pushed to the same remote.

### Syncing Repos

Use `evolve-lite-sync` to pull the latest changes from every configured
repo:

```text
evolve-lite-sync
> Synced 2 repo(s): memory [write] (+0 added, 1 updated, 0 removed), alice [read] (+2 added, 0 updated, 0 removed)
```

Read-scope repos use `git fetch` + `git reset --hard`. Write-scope repos
use `git fetch` + `git rebase` so any unpushed local publish commits are
preserved.

### Unsubscribing

Use `evolve-lite-unsubscribe` to remove a configured repo and delete
its locally cloned files:

```text
evolve-lite-unsubscribe
> Which repo would you like to remove?
> 1. memory [write]
> 2. alice [read]
```

The skill confirms before deleting `.evolve/entities/subscribed/{name}/`.
Removing a write-scope repo will also discard any unpushed local
publish commits, so the skill warns first.

### Sharing Storage Layout

```text
.evolve/
  entities/
    guideline/                      # your private guidelines
      my-guideline.md
    subscribed/
      memory/                       # write-scope clone (publishes land here)
        guideline/
          my-published-guideline.md
      alice/                        # read-scope clone (also serves as recall mirror)
        guideline/
          her-guideline.md
```

## Skills Included

There is no `learn` or `recall` skill — `~/.bob/rules/00-evolve-lite.md` drives
both directly, so a skill would be redundant double-delivery of the same
instructions.

### `evolve-lite-adapt-memory`

Mirror a just-saved memory into the shared Evolve store so it becomes shareable
and auditable alongside every other entity.

### `evolve-lite-provenance`

Analyze saved trajectories and recall-audit events offline to record whether
recalled guidelines influenced completed sessions.

### `evolve-lite-retention`

Apply data-retention rules to the local store — flag or delete stale and unused
entities and expired sessions. Dry-run by default.

### `evolve-lite-save` / `evolve-lite-save-trajectory` / `evolve-lite-synthesize-skill`

Capture the current session: as a reusable skill, as a trajectory JSON file in
OpenAI chat-completion format, or by promoting a saved trajectory into an
executable skill.

### `evolve-lite-publish`

Publish private guidelines to a write-scope repo:
- Lists available private guidelines
- Moves selected guidelines into the write-scope clone at
  `.evolve/entities/subscribed/{repo}/guideline/`
- Stamps with `owner`, `published_at`, and `source` metadata
- Commits and pushes to the configured remote

### `evolve-lite-subscribe`

Add a configured repo to the unified `repos:` list:
- Clones the remote into `.evolve/entities/subscribed/{name}/`
- Adds an entry with `scope: read` or `scope: write` to config

### `evolve-lite-sync`

Sync every configured repo:
- Read-scope: fetch + reset --hard (clobbers any local edits)
- Write-scope: fetch + rebase (preserves unpushed local publishes)
- Reports changes (added, updated, removed)

### `evolve-lite-unsubscribe`

Remove a configured repo:
- Lists current repos with their scope and notes
- Deletes the local clone at `.evolve/entities/subscribed/{name}/`
- Removes the entry from config

## Environment Variables

- `EVOLVE_DIR`: Override the default `.evolve` directory location (guidelines, config, etc. are stored here)

## Verification

After installation, the skills should be available in Bob's skill list.
