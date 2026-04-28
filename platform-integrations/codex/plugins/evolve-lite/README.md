# Evolve Lite Plugin for Codex

A plugin that helps Codex save, recall, and share reusable entities across workspaces.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

## Features

- Automatic recall through a repo-level Codex `UserPromptSubmit` hook when Codex hooks are enabled
- Manual `evolve-lite:learn` skill to save reusable entities into `.evolve/entities/`
- Manual `evolve-lite:recall` skill to inspect everything stored for the current repo
- Manual `evolve-lite:publish` skill to publish private guidelines to your public repo
- Manual `evolve-lite:subscribe` and `evolve-lite:unsubscribe` skills to manage shared guideline repos
- Automatic or manual `evolve-lite:sync` to mirror subscribed repos into local recall storage

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

This source tree intentionally omits `lib/`.

The shared library lives in:

```text
platform-integrations/claude/plugins/evolve-lite/lib/
```

`platform-integrations/install.sh` installs Codex in this order:

1. copy the Codex plugin source into `plugins/evolve-lite/`
2. copy the shared `lib/` from the Claude plugin into `plugins/evolve-lite/lib/`
3. wire the marketplace entry
4. wire the Codex hooks

## Installation

Use the platform installer from the repo root:

```bash
platform-integrations/install.sh install --platform codex
```

That installs:

- `plugins/evolve-lite/`
- `.agents/plugins/marketplace.json`
- `.codex/hooks.json`

Automatic recall requires Codex hooks to be enabled in `~/.codex/config.toml`:

```toml
[features]
codex_hooks = true
```

If you do not want to enable Codex hooks, you can still invoke the installed `evolve-lite:recall` skill manually to load or inspect the saved guidance for the current repo.

The installed Codex hook does not require `git`. It walks upward from the current working directory until it finds the repo-local `plugins/evolve-lite/.../retrieve_entities.py` script.

The installer always registers a `SessionStart` hook with matcher `startup|resume`; it runs on every Codex session start or resume and exits quickly unless `sync.on_session_start` is enabled and at least one repo is configured in `evolve.config.yaml`.

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

### `evolve-lite:learn`

Analyze the current session and save proactive Evolve entities as markdown files.

### `evolve-lite:recall`

Show the entities already stored for the current workspace, including
guidelines pulled from any write- or read-scope repo under
`.evolve/entities/subscribed/`.

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

- `plugins/evolve-lite/` exists in the repo
- `.agents/plugins/marketplace.json` contains the `evolve-lite` entry
- `.codex/hooks.json` contains the Evolve `UserPromptSubmit` and `SessionStart` hooks

You can also run:

```bash
platform-integrations/install.sh status
```

## Plugin Structure

```text
evolve-lite/
├── .codex-plugin/
│   └── plugin.json
├── skills/
│   ├── learn/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── save_entities.py
│   ├── recall/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── retrieve_entities.py
│   ├── publish/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── publish.py
│   ├── subscribe/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── subscribe.py
│   ├── unsubscribe/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── unsubscribe.py
│   └── sync/
│       ├── SKILL.md
│       └── scripts/
│           └── sync.py
├── README.md
└── lib/                       # copied in at install time from the Claude plugin
```
