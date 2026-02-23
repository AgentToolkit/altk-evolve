# Kaizen Plugin for Claude Code

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
1. Loads all stored entities from `.kaizen/entities.json`
2. Formats and injects them into the conversation context
3. Claude applies relevant entities to the current task

### Entity Generation (Manual by Default)

By default, you must manually invoke the `/kaizen:learn` skill to extract entities:
1. Complete a conversation or task
2. Invoke `/kaizen:learn`
3. The plugin analyzes the conversation trajectory
4. Extracts actionable entities from what worked/failed
5. Saves new entities to `.kaizen/entities.json`

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

## Entities Storage

Entities are stored in `.kaizen/entities.json`:

```json
{
  "entities": [
    {
      "content": "Use Python PIL/Pillow for image metadata extraction in sandboxed environments",
      "rationale": "System tools like exiftool may not be available",
      "category": "strategy",
      "trigger": "When extracting image metadata in containerized environments"
    }
  ]
}
```

## Environment Variables

- `KAIZEN_ENTITIES_FILE`: Override the default entities storage location
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
│   └── save/
│       └── SKILL.md
├── hooks/
│   └── hooks.json               # Auto-configured hooks
└── README.md
```

## License

MIT
