# Evolve Lite Plugin for Codex

Evolve Lite for Codex provides lightweight file-backed learning and recall without MCP.

## Features

- Automatic recall through a repo-level Codex `UserPromptSubmit` hook when Codex hooks are enabled
- Manual `evolve:learn` skill to save reusable entities into `.evolve/entities/`
- Manual `evolve:recall` skill to inspect everything stored for the current repo

## Storage

Entities are stored in the active workspace under:

```text
.evolve/entities/
  guideline/
    use-context-managers-for-file-operations.md
    cache-api-responses-locally.md
```

Each entity is a markdown file with lightweight YAML frontmatter.

## Source Layout

This source tree intentionally omits `lib/`.

The shared library lives in:

```text
platform-integrations/claude/plugins/evolve-lite/lib/
```

`platform-integrations/install.sh` copies that shared library into the installed Codex plugin so the installed layout is self-contained.

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

If you do not want to enable Codex hooks, you can still invoke the installed `evolve:recall` skill manually to load or inspect the saved guidance for the current repo.

The installed Codex hook does not require `git`. It walks upward from the current working directory until it finds the repo-local `plugins/evolve-lite/.../retrieve_entities.py` script.

## Included Skills

### `evolve:learn`

Analyze the current session and save proactive Evolve entities as markdown files.

### `evolve:recall`

Show the entities already stored for the current workspace.
