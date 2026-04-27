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

Evolve Lite treats shared guidelines as multi-reader / multi-writer git
databases. A single unified `repos:` list in `evolve.config.yaml` describes
every external guideline repo you read from or publish to; each entry has a
`scope` of `read` (subscribe only) or `write` (publish target, also synced).

### Setup

Sharing requires an `evolve.config.yaml` at the project root. The subscribe
and publish skills will help you create one if it is missing. Structure:

```yaml
identity:
  user: yourname          # used to stamp ownership on published guidelines
repos:
  - name: memory
    scope: write
    remote: git@github.com:yourname/evolve-memory.git
    branch: main
    notes: public memory for my open-source projects
  - name: org-memory
    scope: read
    remote: git@github.com:acme/org-memory.git
    branch: main
    notes: private memory shared only within my org
sync:
  on_session_start: true  # auto-sync on each session start
```

- `scope: read` — pulled on sync. Cannot be published to.
- `scope: write` — publish target **and** pulled on sync (so you see
  everything pushed to it, including by other writers).

The `.evolve/` directory is kept out of version control — the skills
automatically add it to `.gitignore`.

### Subscribing to a Repo

Use `/evolve-lite:subscribe` to add either a read-only subscription or a
write-scope publish target:

```text
/evolve-lite:subscribe
> Remote URL: git@github.com:alice/evolve-guidelines.git
> Short name: alice
> Scope: read
```

The repo is cloned into `.evolve/entities/subscribed/alice/` so recall can
pick it up immediately. Repo names must use only letters, numbers, `.`,
`_`, and `-`.

### Publishing Guidelines

Use `/evolve-lite:publish` to share one or more of your local guidelines
via a **write-scope** repo:

1. The skill selects (or asks about) the write-scope target repo
2. It lists files in `.evolve/entities/guideline/`
3. You pick which ones to publish
4. Each selected file is moved into `.evolve/entities/subscribed/{repo}/guideline/`,
   stamped with `visibility: public`, `owner`, `published_at`, and
   `source`, committed, and pushed to the remote

Because the publish target is also a subscribed repo, your next sync will
pull in anything other writers have pushed to the same repo.

### Syncing Repos

Use `/evolve-lite:sync` to pull the latest changes from every configured
repo (both scopes):

```text
/evolve-lite:sync
> Synced 2 repo(s): memory [write] (+2 added, 0 updated, 0 removed), bob [read] (+0 added, 1 updated, 0 removed)
```

If `sync.on_session_start: true` is set in config, this runs automatically
at the start of each session.

> **Note:** Read-scope repos use `git fetch` + `git reset --hard`, so the
> local clone always matches the remote exactly (deleted or modified files
> are restored). Write-scope repos use `git fetch` + `git rebase` so any
> unpushed local publish commits are preserved.

### Removing a Repo

Use `/evolve-lite:unsubscribe` to remove any repo and delete its local
clone. The skill shows scope and notes for each configured repo and warns
before removing a write-scope repo (unpushed publishes would be lost).

### Sharing Storage Layout

```text
.evolve/
  entities/
    guideline/
      private-guideline.md
    subscribed/
      memory/                 # write-scope clone — publishes land here
        guideline/
          my-published-guideline.md
      alice/                  # read-scope clone
        guideline/
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
│   ├── subscribe/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── subscribe.py
│   ├── publish/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── publish.py
│   ├── sync/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── sync.py
│   ├── unsubscribe/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── unsubscribe.py
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
