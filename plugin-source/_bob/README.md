# Evolve Lite for Bob

A Bob integration that gives the agent a persistent memory of your project,
plus git-backed sharing of guidelines.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

## Features

Recall and save are driven by instructions, not skills. The installer writes
`EVOLVE.md` into Bob's global rules directory, where it is loaded into every
session; it tells the agent to read `./.evolve/entities/` as its first action on
a non-trivial task, and to write an entity near the end only when it learned
something durable.

The commands cover everything around that loop — `/evolve-lite-publish`, `/evolve-lite-subscribe`, `/evolve-lite-sync`,
`/evolve-lite-unsubscribe`, `/evolve-lite-provenance`, `/evolve-lite-retention`,
`/evolve-lite-save`, `/evolve-lite-save-trajectory`,
`/evolve-lite-synthesize-skill`. `/evolve-lite-adapt-memory` also ships, but
is a no-op on Bob (see [Skills Included](#skills-included)).

> Skill folders and slash commands use the dash form (`/evolve-lite-publish`).
> Each skill's frontmatter `name:`, the skill bodies, and script messages use
> the colon form (`evolve-lite:publish`), as on other hosts.

## Installation

Run the installer from your project root:

```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform bob --mode lite
```

or, from a clone of this repo, pass your project with `--dir` (it defaults to
the current directory, so running it from the altk-evolve checkout without
`--dir` installs into the checkout itself):

```bash
bash platform-integrations/install.sh install --platform bob --mode lite --dir /path/to/your/project
```

This installs, per project (under `<project>/.bob/`):
- 10 skills in `<project>/.bob/skills/` and a matching slash command per skill
  in `<project>/.bob/commands/`
- Shared library in `<project>/.bob/lib/evolve-lite/`

and, globally (under `~/.bob/`):
- The always-on instructions at `~/.bob/rules/00-evolve-lite.md` (Bob loads every
  `~/.bob/rules/*.md` into every session)
- The recall-audit script at `~/.bob/evolve-lite/audit_recall.py`, allowlisted in
  `~/.bob/settings.json` so it runs without a prompt

## How It Works

### Guideline Storage

Entities are stored as individual markdown files in `.evolve/entities/`. What
the agent saves (per the "Save" section of the instructions) lands under one
directory per type — `user`, `feedback`, `project` or `reference`. Both
read-scope subscriptions and write-scope publish targets live under
`entities/subscribed/{name}/`:

```text
.evolve/entities/
  project/                              # Saved by the agent, one dir per type
    use-context-managers.md
  guideline/                            # What publish reads (see below)
    private-guideline.md
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
type: project
trigger: When processing files or managing resources
---

Use context managers for file operations.

**Why:** they guarantee the file is closed even when an exception is raised.
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
  on_session_start: false   # not acted on: sync runs only when you invoke it
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

Use `evolve-lite-publish` to share local entities via a **write-scope** repo:

1. The skill picks (or asks about) the write-scope target repo
2. Lists files in each `.evolve/entities/{type}/` directory (skipping `subscribed/`)
3. You pick which ones to publish
4. Each selected file is moved into the write-scope clone at
   `.evolve/entities/subscribed/{repo}/{type}/`, stamped with your
   username, committed, and pushed to the remote

Because the publish target is also a subscribed repo, your next sync
pulls in anything other writers have pushed to the same remote.

Entities the agent saves under `entities/{user,feedback,project,reference}/` are
publishable this way. If the same file name exists in more than one type
directory, publish refuses and asks for `--type`.

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
    project/                        # saved by the agent, one dir per type
      my-entity.md
    guideline/                      # what publish reads
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

Ships for parity with Claude, but is a no-op on Bob: it mirrors a host's
native memory store, and Bob has none. Bob saves entities directly by
following the "Save" section of `~/.bob/rules/00-evolve-lite.md`.

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

- `EVOLVE_DEBUG`: set to any non-empty value to append debug lines from the
  helper scripts to `$TMPDIR/evolve-<uid>/evolve-plugin.log`.
- `EVOLVE_DIR`: **not supported end to end yet.** Some scripts read it, but the
  always-on instructions always tell the agent to use `./.evolve/`, and
  `subscribe` and `sync` resolve the project root from it differently. Leave
  it unset.

## Verification

After installation, the skills should be available in Bob's skill list.
