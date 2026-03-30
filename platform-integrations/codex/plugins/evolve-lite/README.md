# Evolve Lite Plugin for Codex

Evolve Lite for Codex provides lightweight file-backed learning and recall without MCP.

## Features

- Automatic recall through a repo-level Codex `UserPromptSubmit` hook
- Manual `learn` skill to save reusable entities into `.evolve/entities/`
- Manual `recall` skill to inspect everything stored for the current repo

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

## Included Skills

### `learn`

Analyze the current session and save proactive Evolve entities as markdown files.

### `recall`

Show the entities already stored for the current workspace.
