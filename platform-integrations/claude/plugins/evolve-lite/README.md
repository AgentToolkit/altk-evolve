# Evolve Lite Plugin for Claude Code

A plugin that helps Claude Code learn from conversations by automatically extracting and applying entities.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

## Features

- **Automatic Retrieval**: At the start of each prompt, relevant entities are automatically injected
- **Manual Learning**: Use the `/evolve-lite:learn` skill to extract and save entities from conversations
- **Automatic Learning**: After each task, entities are automatically extracted and saved via a Stop hook
- **Zero-config Retrieval**: Hooks are automatically installed when the plugin is enabled

## Installation

### From Local Directory

```bash
claude --plugin-dir /path/to/altk-evolve/platform-integrations/claude/plugins/evolve-lite
```

### From Marketplace

1. Add the marketplace and plugin:
   ```bash
   claude plugin marketplace add AgentToolkit/altk-evolve
   claude plugin install evolve@evolve-marketplace
   ```


## How It Works

### Entity Retrieval (Automatic)

When you submit a prompt, the plugin automatically:
1. Loads all stored entities from `.evolve/entities/` (one markdown file per entity)
2. Formats and injects them into the conversation context
3. Claude applies relevant entities to the current task

### Entity Generation (Automatic)

After Claude completes each task, the plugin automatically invokes the `/evolve-lite:learn` skill via a `Stop` hook:
1. Claude finishes responding to your prompt
2. The Stop hook triggers and instructs Claude to run `/evolve-lite:learn`
3. The plugin analyzes the conversation trajectory
4. Extracts actionable entities from what worked/failed
5. Saves new entities as markdown files in `.evolve/entities/{type}/`

You can also manually invoke `/evolve-lite:learn` at any time.

> **UX note:** The Stop hook has an empty matcher (`""`), meaning it fires after *every* task and can add up to ~2 minutes of delay per interaction (the hook's `timeout` is 120s). It also invokes the Claude API on each stop, which incurs additional cost. Learned entities are stored as markdown files in `.evolve/entities/{type}/` — inspect or remove them there at any time.
>
> **To disable or limit automatic learning**, edit `hooks/hooks.json` inside the plugin directory:
> - Remove the entire `"Stop"` block to turn off auto-learning entirely.
> - Set a specific `"matcher"` string to restrict triggering to prompts that contain that text.
> - Reduce `"timeout"` to cap how long the learn step can run.

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
sync:
  on_session_start: true  # auto-sync on each session start
```

The `.evolve/` directory is kept out of version control — the skills automatically add it to `.gitignore`.

### Publishing Guidelines

Use `/evolve-lite:publish` to share one or more of your local guidelines with others:

1. The skill lists files in `.evolve/entities/guideline/`
2. You pick which ones to publish
3. Each selected file is copied to `.evolve/public/`, stamped with your username as the owner, committed, and pushed to your `public_repo.remote`

Others can then subscribe using that remote URL.

### Subscribing to Guidelines

Use `/evolve-lite:subscribe` to pull in guidelines from another user's public repo:

```
/evolve-lite:subscribe
> Remote URL: git@github.com:alice/evolve-guidelines.git
> Short name: alice
```

The repo is cloned to `.evolve/subscribed/alice/` and mirrored into `.evolve/entities/subscribed/alice/` so recall picks them up immediately.

### Syncing Subscriptions

Use `/evolve-lite:sync` to pull the latest changes from all subscribed repos:

```
/evolve-lite:sync
> Synced 2 repo(s): alice (+2 added, 0 updated, 0 removed), bob (+0 added, 1 updated, 0 removed)
```

If `sync.on_session_start: true` is set in config, this runs automatically at the start of each session.

### Unsubscribing

Use `/evolve-lite:unsubscribe` to remove a subscription and delete its locally cloned files:

```
/evolve-lite:unsubscribe
> Which subscription would you like to remove?
> 1. alice
> 2. bob
```

The skill confirms before deleting `.evolve/subscribed/{name}/` and its mirror under `.evolve/entities/subscribed/{name}/`.

### Sharing Storage Layout

```text
.evolve/
  public/                     # git repo pushed to your public remote
    guideline-name.md         # owner-stamped guideline
  subscribed/
    alice/                    # git clone of alice's public repo
      her-guideline.md
  entities/
    subscribed/
      alice/                  # mirrored for recall
        her-guideline.md
```

## Example Walkthrough

See the [Evolve Lite guide](../../../../docs/integrations/claude/evolve-lite.md#example-walkthrough) for a step-by-step example showing the full learn-then-recall loop across two sessions.

## Skills Included

### `/evolve-lite:learn`

Manually invoke to extract entities from the current conversation:
- Analyzes task, steps taken, successes and failures
- Generates proactive entities (what to do, not what to avoid)
- Outputs JSON that the save script persists as entity files

### `/evolve-lite:recall`

Manually invoke to retrieve and display stored entities.

### `/evolve-lite:save`

Manually invoke to capture successful workflows from your current session and save them as reusable skills:
- Analyzes conversation history (user requests, reasoning, tool calls, responses)
- Generates parameterized SKILL.md documentation
- Creates Python helper scripts for programmatic operations (when applicable)
- Saves to `~/.claude/skills/{skill-name}/` for cross-project availability

**Quick Start:**
```
User: [Complete a successful task]
User: "save"
Assistant: "What would you like to name this skill?"
User: "my-workflow-name"
```

### `/evolve-lite:save-trajectory`

Manually invoke to export the current conversation as a trajectory JSON file:
- Converts all messages to OpenAI chat completion format (user, assistant, tool calls, tool results)
- Strips system reminders and cleans content
- Saves to `.evolve/trajectories/` with a timestamped filename
- Useful for trajectory analysis, fine-tuning data collection, and session review
- Runs in a forked context to keep the parent conversation clean

## Entities Storage

Entities are stored as individual markdown files in `.evolve/entities/`, nested by type:

```
.evolve/entities/
  guideline/
    use-python-pil-for-image-metadata-extraction.md
    cache-api-responses-locally.md
```

Each file uses markdown with YAML frontmatter:

```markdown
---
type: guideline
trigger: When extracting image metadata in containerized environments
---

Use Python PIL/Pillow for image metadata extraction in sandboxed environments

## Rationale

System tools like exiftool may not be available
```

## Environment Variables

- `EVOLVE_DIR`: Override the default `.evolve` directory location (entities, trajectories, config, etc. are stored here)

## Verification

After installation, run `claude plugin list` to confirm the plugin is enabled.

## Plugin Structure

```text
evolve/
├── .claude-plugin/
│   └── plugin.json              # Plugin manifest
├── skills/
│   ├── learn/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── save_entities.py
│   ├── recall/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── retrieve_entities.py
│   ├── save/
│   │   └── SKILL.md
│   └── save-trajectory/
│       ├── SKILL.md
│       └── scripts/
│           └── save_trajectory.py
├── hooks/
│   └── hooks.json               # Auto-configured hooks
└── README.md
```
