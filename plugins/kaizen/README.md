# Guidelines Plugin for Claude Code

A plugin that helps Claude Code learn from conversations by automatically extracting and applying guidelines.

## Features

- **Automatic Retrieval**: At the start of each prompt, relevant guidelines are automatically injected
- **Manual Learning**: Use the `/kaizen:learn` skill to extract and save guidelines from conversations
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

### Guideline Retrieval (Automatic)

When you submit a prompt, the plugin automatically:
1. Loads all stored guidelines from `.claude/guidelines.json`
2. Formats and injects them into the conversation context
3. Claude applies relevant guidelines to the current task

### Guideline Generation (Manual by Default)

By default, you must manually invoke the `/kaizen:learn` skill to extract guidelines:
1. Complete a conversation or task
2. Invoke `/kaizen:learn` 
3. The plugin analyzes the conversation trajectory
4. Extracts actionable guidelines from what worked/failed
5. Saves new guidelines to `.claude/guidelines.json`

## Skills Included

### `/kaizen:learn`

Manually invoke to extract guidelines from the current conversation:
- Analyzes task, steps taken, successes and failures
- Generates proactive guidelines (what to do, not what to avoid)
- Outputs JSON for storage

### `/kaizen:recall`

Manually invoke to retrieve and display stored guidelines.

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

See [SAVE_SKILL.md](SAVE_SKILL.md) for detailed documentation.

## Guidelines Storage

Guidelines are stored in `.claude/guidelines.json`:

```json
{
  "guidelines": [
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

- `GUIDELINES_FILE`: Override the default guidelines storage location
- `CLAUDE_PROJECT_ROOT`: Set by Claude Code, used to locate project-level guidelines

## Verification

After installation, run `claude plugin list` to confirm the plugin is enabled.

## Plugin Structure

```text
kaizen/
├── .claude-plugin/
│   └── plugin.json              # Plugin manifest
├── skills/
│   ├── learn/
│   │   └── SKILL.md
│   ├── recall/
│   │   └── SKILL.md
│   └── save/
│       └── SKILL.md
├── hooks/
│   └── hooks.json               # Auto-configured hooks
├── scripts/
│   ├── save_guidelines.py
│   └── retrieve_guidelines.py
└── README.md
```

## License

MIT
