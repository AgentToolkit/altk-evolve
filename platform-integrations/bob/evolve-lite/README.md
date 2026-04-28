# Evolve Lite for Bob

A Bob integration that helps you learn from conversations by automatically extracting and applying guidelines.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

## Features

- **Manual Learning**: Use `evolve-lite:learn` to extract and save guidelines from conversations
- **Manual Retrieval**: Use `evolve-lite:recall` to retrieve and apply stored guidelines
- **Guideline Sharing**: Publish your guidelines and subscribe to others' via Git repositories

## Installation

Run the installation script from the repository root:

```bash
bash platform-integrations/install.sh install bob lite
```

This installs:
- 6 skills in `~/.bob/skills/`
- Shared library in `~/.bob/evolve-lib/`
- Custom mode configuration

## How It Works

### Guideline Storage

Guidelines are stored as individual markdown files in `.evolve/entities/`, organized by source:

```text
.evolve/entities/
  guideline/                    # Private guidelines
    use-context-managers.md
  public/                       # Your published guidelines
    best-practice.md
  subscribed/
    alice/                      # Guidelines from alice
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

Evolve Lite supports sharing guidelines between users via public Git repositories. You can publish your own guidelines so others can subscribe to them, and subscribe to guidelines published by others.

### Setup

Sharing requires an `evolve.config.yaml` at the project root. If it doesn't exist, the subscribe or publish skills will prompt you to create one. Minimal structure:

```yaml
identity:
  user: yourname          # used to stamp ownership on published guidelines
public_repo:
  remote: git@github.com:yourname/evolve-guidelines.git
  branch: main
subscriptions: []
```

The `.evolve/` directory is kept out of version control — the skills automatically add it to `.gitignore`.

### Publishing Guidelines

Use `evolve-lite:publish` to share one or more of your local guidelines with others:

1. The skill lists files in `.evolve/entities/guideline/`
2. You pick which ones to publish
3. Each selected file is moved to `.evolve/public/guideline/`, stamped with your username as the owner, committed, and pushed to your `public_repo.remote`

Others can then subscribe using that remote URL.

### Subscribing to Guidelines

Use `evolve-lite:subscribe` to pull in guidelines from another user's public repo:

```text
evolve-lite:subscribe
> Remote URL: git@github.com:alice/evolve-guidelines.git
> Short name: alice
```

The repo is cloned directly into `.evolve/entities/subscribed/alice/` (this directory serves as both the git clone and the recall mirror).

### Syncing Subscriptions

Use `evolve-lite:sync` to pull the latest changes from all subscribed repos:

```text
evolve-lite:sync
> Synced 2 repo(s): alice (+2 added, 0 updated, 0 removed), bob (+0 added, 1 updated, 0 removed)
```

### Unsubscribing

Use `evolve-lite:unsubscribe` to remove a subscription and delete its locally cloned files:

```text
evolve-lite:unsubscribe
> Which subscription would you like to remove?
> 1. alice
> 2. bob
```

The skill confirms before deleting `.evolve/entities/subscribed/{name}/`.

### Sharing Storage Layout

```text
.evolve/
  public/                     # git repo pushed to your public remote
    guideline/
      guideline-name.md       # owner-stamped guideline
  entities/
    guideline/                # your private guidelines
      my-guideline.md
    subscribed/
      alice/                  # git clone (also serves as recall mirror)
        guideline/
          her-guideline.md
```

## Skills Included

### `evolve-lite:learn`

Manually invoke to extract guidelines from the current conversation:
- Analyzes task, steps taken, successes and failures
- Generates proactive guidelines (what to do, not what to avoid)
- Saves guidelines as markdown files in `.evolve/entities/guideline/`

### `evolve-lite:recall`

Manually invoke to retrieve and display stored guidelines:
- Loads guidelines from private, public, and subscribed sources
- Formats and displays them for your review
- Annotates subscribed guidelines with their source

### `evolve-lite:publish`

Publish private guidelines to your public repository:
- Lists available private guidelines
- Moves selected guidelines to `.evolve/public/`
- Stamps with owner and published_at metadata
- Commits and pushes to your public remote

### `evolve-lite:subscribe`

Subscribe to another user's public guidelines:
- Clones their public repository
- Mirrors guidelines to `.evolve/entities/subscribed/`
- Adds subscription to config

### `evolve-lite:sync`

Sync all subscribed repositories:
- Pulls latest changes from each subscription
- Updates mirrored guidelines
- Reports changes (added, updated, removed)

### `evolve-lite:unsubscribe`

Remove a subscription:
- Lists current subscriptions
- Deletes selected subscription's local files
- Removes from config

## Environment Variables

- `EVOLVE_DIR`: Override the default `.evolve` directory location (guidelines, config, etc. are stored here)

## Verification

After installation, the skills should be available in Bob's skill list.