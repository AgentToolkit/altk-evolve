# Kaizen Lite Plugin for Claude Code

A plugin that helps Claude Code learn from conversations by automatically extracting and applying entities.

## Features

- **Automatic Retrieval**: At the start of each prompt, relevant entities are automatically injected
- **Manual Learning**: Use the `/kaizen:learn` skill to extract and save entities from conversations
- **Zero-config Retrieval**: Hooks are automatically installed when the plugin is enabled

## Installation

### From Local Directory

```bash
claude --plugin-dir /path/to/kaizen/repo/plugins/kaizen
```

### From Marketplace

1. Add the marketplace and plugin:
   ```bash
   claude plugin marketplace add AgentToolkit/kaizen
   claude plugin install kaizen@kaizen-marketplace
   ```


## How It Works

### Entity Retrieval (Automatic)

When you submit a prompt, the plugin automatically:
1. Loads all stored entities from `.kaizen/entities/` (one markdown file per entity)
2. Formats and injects them into the conversation context
3. Claude applies relevant entities to the current task

### Entity Generation (Manual by Default)

By default, you must manually invoke the `/kaizen:learn` skill to extract entities:
1. Complete a conversation or task
2. Invoke `/kaizen:learn`
3. The plugin analyzes the conversation trajectory
4. Extracts actionable entities from what worked/failed
5. Saves new entities as markdown files in `.kaizen/entities/{type}/`

## Example Walkthrough

See [KAIZEN_LITE.md](../../KAIZEN_LITE.md#example-walkthrough) for a step-by-step example showing the full learn-then-recall loop across two sessions.

## Skills Included

### `/kaizen:learn`

Manually invoke to extract entities from the current conversation:
- Analyzes task, steps taken, successes and failures
- Generates proactive entities (what to do, not what to avoid)
- Outputs JSON for storage

### `/kaizen:recall`

Manually invoke to retrieve and display stored entities.

### `/kaizen:save`

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

### `/kaizen:save-trajectory`

Manually invoke to export the current conversation as a trajectory JSON file:
- Converts all messages to OpenAI chat completion format (user, assistant, tool calls, tool results)
- Strips system reminders and cleans content
- Saves to `.kaizen/trajectories/` with a timestamped filename
- Useful for trajectory analysis, fine-tuning data collection, and session review
- Runs in a forked context to keep the parent conversation clean

## Entities Storage

Entities are stored as individual markdown files in `.kaizen/entities/`, nested by type:

```
.kaizen/entities/
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

- `KAIZEN_ENTITIES_DIR`: Override the default entities directory location
- `CLAUDE_PROJECT_ROOT`: Set by Claude Code, used to locate project-level entities

## Verification

After installation, run `claude plugin list` to confirm the plugin is enabled.

## Plugin Structure

```text
kaizen/
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

## License

MIT
