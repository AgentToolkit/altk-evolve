# Evolve Lite Plugin for Codex

A plugin that helps Codex save, recall, and share reusable entities across workspaces.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

## Features

Recall and save are driven by instructions, not skills and not hooks. The
installer writes `EVOLVE.md` — a self-directed memory contract telling the agent
to read `./.evolve/entities/` as its first action on a non-trivial task, and to
write an entity near the end only when it learned something durable — and points
`~/.codex/AGENTS.md` at it. This plugin installs **no hooks** on any platform.

The skills cover everything around that loop:

- `evolve-lite:adapt-memory` — mirror a saved memory into the shared store
- `evolve-lite:publish` — publish a private guideline to a write-scope repo
- `evolve-lite:subscribe` / `evolve-lite:unsubscribe` — manage shared guideline repos
- `evolve-lite:sync` — pull the latest from every configured repo
- `evolve-lite:provenance` — report whether recalled entities influenced past sessions
- `evolve-lite:retention` — flag or delete stale entities (dry-run by default)
- `evolve-lite:save`, `evolve-lite:save-trajectory`, `evolve-lite:synthesize-skill` — capture a session's workflow

## Storage

Entities and sharing data are stored in the active workspace under:

```text
.evolve/
  entities/
    guideline/
      use-context-managers-for-file-operations.md     # private
    subscribed/
      memory/                                          # write-scope clone (publish target)
        guideline/
          my-published-guideline.md
      alice/                                           # read-scope clone
        guideline/
          prefer-small-functions.md
  audit.log
```

Each entity is a markdown file with lightweight YAML frontmatter.

Sharing configuration lives in `evolve.config.yaml` at the repo root, as a
single unified list of repos (both read- and write-scope):

```yaml
identity:
  user: alice

repos:
  - name: memory
    scope: write
    remote: git@github.com:alice/evolve-memory.git
    branch: main
    notes: public memory for foobar project
  - name: team
    scope: read
    remote: git@github.com:myorg/evolve-guidelines.git
    branch: main

sync:
  on_session_start: true
```

## Source Layout

This tree is generated. Do not edit it — the source of truth is
`plugin-source/`, rendered by `plugin-source/build_plugins.py` (`just
compile-plugins`). Files under `plugin-source/_codex/` ship to Codex only;
everything else — the skills, `EVOLVE.md`, and the shared `lib/evolve-lite/` —
is shared across hosts and rendered into each one's tree, including this one.

## Installation

Use the platform installer from the repo root:

```bash
platform-integrations/install.sh install --platform codex
```

That does four things:

1. copies the plugin to `plugins/evolve-lite/` (including `lib/evolve-lite/`)
2. upserts the `evolve-lite` entry in `.agents/plugins/marketplace.json`
3. writes `~/.codex/evolve-lite/EVOLVE.md` and injects a single pointer line
   into `~/.codex/AGENTS.md` — Codex reads `AGENTS.md` verbatim and has no
   `@`-import, so the pointer tells the agent to read that file on demand
4. installs `~/.codex/evolve-lite/audit_recall.py` at that global path, because
   `EVOLVE.md` names it absolutely

No hooks are registered and no `~/.codex/config.toml` changes are needed.

## Sharing Guidelines

Evolve Lite treats shared guidelines as multi-reader / multi-writer git
databases. A single unified `repos:` list in `evolve.config.yaml` describes
every external guideline repo; each entry has a `scope` of `read` (subscribe
only) or `write` (publish target that is also pulled on sync).

### Setup

Sharing uses `evolve.config.yaml` at the project root. Minimal structure:

```yaml
identity:
  user: yourname

repos:
  - name: memory
    scope: write
    remote: git@github.com:yourname/evolve-memory.git
    branch: main
    notes: public memory for my open-source projects

sync:
  on_session_start: true
```

The `.evolve/` directory is kept out of version control.

### Subscribing to a Repo

Use `evolve-lite:subscribe` to add either a read-only subscription or a
write-scope publish target. The repo is cloned directly into
`.evolve/entities/subscribed/{name}/` so recall picks it up immediately.
Names must use only letters, numbers, `.`, `_`, and `-`.

### Publishing Guidelines

Use `evolve-lite:publish` to share local guidelines via a **write-scope**
repo:

1. The skill selects (or asks about) the write-scope target repo
2. Pick a file from `.evolve/entities/guideline/`
3. Publish moves it into `.evolve/entities/subscribed/{repo}/guideline/`,
   stamps it with `visibility: public`, `published_at`, `owner`, and a
   `source` label derived from the repo's remote
4. The original private guideline is removed from
   `.evolve/entities/guideline/`

Because the publish target is also a subscribed repo, your next sync pulls
in anything other writers have pushed to the same remote.

### Syncing Repos

Use `evolve-lite:sync` to pull the latest changes from every configured
repo (both scopes). Read-scope repos use `git fetch` + `git reset --hard`;
write-scope repos use `git fetch` + `git rebase` so unpushed local publish
commits are preserved.

If `sync.on_session_start: true` is set in config, this runs automatically
whenever a Codex session starts or resumes.

### Removing a Repo

Use `evolve-lite:unsubscribe` to remove any configured repo and delete its
local clone at `.evolve/entities/subscribed/{name}/`.

### Sharing Storage Layout

```text
.evolve/
  entities/
    guideline/
      private-guideline.md      # private local guideline
    subscribed/
      memory/                   # write-scope clone — publishes land here
        guideline/
          my-published-guideline.md
      alice/                    # read-scope clone
        guideline/
          her-guideline.md      # recall annotates as [from: alice]
```

## Example Walkthrough

See the [Codex example walkthrough](../../../../docs/examples/hello_world/codex.md) for a step-by-step example showing the save-then-recall loop in a Codex workspace.

## Included Skills

There is no `learn` or `recall` skill — `EVOLVE.md` drives both directly, so a
skill would be redundant double-delivery of the same instructions.

### `evolve-lite:adapt-memory`

Mirror a just-saved memory into the shared Evolve store so it becomes shareable
and auditable alongside every other entity.

### `evolve-lite:provenance`

Analyze saved trajectories and recall-audit events offline to record whether
recalled entities influenced completed sessions.

### `evolve-lite:retention`

Apply data-retention rules to the local store — flag or delete stale and unused
entities and expired sessions. Dry-run by default.

### `evolve-lite:save` / `evolve-lite:save-trajectory` / `evolve-lite:synthesize-skill`

Capture the current session: as a reusable skill, as a trajectory JSON file in
OpenAI chat-completion format, or by promoting a saved trajectory into an
executable skill.

### `evolve-lite:publish`

Move a selected private guideline into a configured write-scope repo's
local clone at `.evolve/entities/subscribed/{repo}/guideline/`, stamp it
as public, commit it, and push it.

### `evolve-lite:subscribe`

Add an entry to the unified `repos:` list (read- or write-scope) and clone
the remote into `.evolve/entities/subscribed/{name}/`.

### `evolve-lite:unsubscribe`

Remove a configured repo from `repos:` and delete its local clone.

### `evolve-lite:sync`

Pull the latest from every configured repo (both scopes). Write-scope
repos use rebase to preserve unpushed local publish commits; read-scope
repos use hard reset to mirror the remote exactly.

## Environment Variables

- `EVOLVE_DIR`: Override the default `.evolve` directory location for entities, sharing data, audit logs, and the mirrored subscription store.

## Verification

After installation, verify that:

- `plugins/evolve-lite/` exists in the repo, with `lib/evolve-lite/entity_io.py` inside it
- `.agents/plugins/marketplace.json` contains the `evolve-lite` entry
- `~/.codex/AGENTS.md` contains the Evolve pointer line, and
  `~/.codex/evolve-lite/` holds `EVOLVE.md` plus `audit_recall.py`

You can also run:

```bash
platform-integrations/install.sh status
```

## Plugin Structure

```text
evolve-lite/
├── .codex-plugin/
│   └── plugin.json
├── EVOLVE.md                        # self-directed memory contract; copied to
│                                    # ~/.codex/evolve-lite/ at install time
├── lib/
│   └── evolve-lite/                 # shared helpers used by the skill scripts
│       ├── audit_recall.py
│       ├── audit.py
│       ├── config.py
│       ├── entity_io.py
│       └── retention.py
├── skills/
│   └── evolve-lite/                 # namespace dir → skills are `evolve-lite:<name>`
│       ├── adapt-memory/
│       ├── provenance/
│       ├── publish/
│       ├── retention/
│       ├── save/
│       ├── save-trajectory/
│       ├── subscribe/
│       ├── sync/
│       ├── synthesize-skill/
│       └── unsubscribe/             # each: SKILL.md [+ scripts/]
└── README.md
```
