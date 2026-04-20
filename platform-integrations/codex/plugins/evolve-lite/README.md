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
      use-context-managers-for-file-operations.md
    subscribed/
      alice/
        guideline/
          prefer-small-functions.md
  public/
    guideline/
      no-eval.md
  subscribed/
    alice/
      guideline/
        prefer-small-functions.md
  audit.log
```

Each entity is a markdown file with lightweight YAML frontmatter.

Sharing configuration lives in `evolve.config.yaml` at the repo root:

```yaml
identity:
  user: alice

public_repo:
  remote: git@github.com:alice/evolve-guidelines.git
  branch: main

subscriptions:
  - name: team
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

The installer always registers a `SessionStart` hook with matcher `startup|resume`; it runs on every Codex session start or resume and exits quickly unless `sync.on_session_start` is enabled and subscriptions are configured in `evolve.config.yaml`.

## Sharing Guidelines

Evolve Lite supports sharing guidelines between users via public Git repositories. You can publish your own guidelines so others can subscribe to them, and subscribe to guidelines published by others.

### Setup

Sharing uses `evolve.config.yaml` at the project root. Minimal structure:

```yaml
identity:
  user: yourname

public_repo:
  remote: git@github.com:yourname/evolve-guidelines.git
  branch: main

subscriptions: []

sync:
  on_session_start: true
```

The `.evolve/` directory is kept out of version control.

### Publishing Guidelines

Use `evolve-lite:publish` to share one or more of your local guidelines with others:

1. Pick a file from `.evolve/entities/guideline/`
2. Publish it into `.evolve/public/guideline/`
3. The published file is stamped with `visibility: public`, `published_at`, and a `source` label derived from config when available
4. The original private guideline is removed from `.evolve/entities/guideline/`

Others can then subscribe using that public remote URL.

### Subscribing to Guidelines

Use `evolve-lite:subscribe` to pull in guidelines from another user's public repo.

The repo is cloned to `.evolve/subscribed/{name}/` and mirrored into `.evolve/entities/subscribed/{name}/` so recall can pick them up immediately.

### Syncing Subscriptions

Use `evolve-lite:sync` to pull the latest changes from all subscribed repos.

If `sync.on_session_start: true` is set in config, this runs automatically whenever a Codex session starts or resumes.

### Unsubscribing

Use `evolve-lite:unsubscribe` to remove a subscription and delete its locally cloned files.

This removes both `.evolve/subscribed/{name}/` and its mirrored recall copy under `.evolve/entities/subscribed/{name}/`.

### Sharing Storage Layout

```text
.evolve/
  public/
    guideline/
      guideline-name.md         # published guideline, included in recall
  subscribed/
    alice/
      guideline/
        her-guideline.md        # git clone of alice's public repo
  entities/
    guideline/
      private-guideline.md      # private local guideline
    subscribed/
      alice/
        guideline/
          her-guideline.md      # mirrored for recall, annotated [from: alice]
```

## Example Walkthrough

See the [Codex example walkthrough](../../../../docs/examples/hello_world/codex.md) for a step-by-step example showing the save-then-recall loop in a Codex workspace.

## Included Skills

### `evolve-lite:learn`

Analyze the current session and save proactive Evolve entities as markdown files.

### `evolve-lite:recall`

Show the entities already stored for the current workspace, including published guidelines under `.evolve/public/`.

### `evolve-lite:publish`

Move selected private guidelines into `.evolve/public/`, stamp them as public, and push them to your configured sharing repo.

### `evolve-lite:subscribe`

Clone another user's public guideline repo into `.evolve/subscribed/` and register it in `evolve.config.yaml`.

### `evolve-lite:unsubscribe`

Remove a configured subscription and delete its local clones and mirrored recall entities.

### `evolve-lite:sync`

Pull every configured subscription and mirror its markdown files into `.evolve/entities/subscribed/` so recall can include them automatically.

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
