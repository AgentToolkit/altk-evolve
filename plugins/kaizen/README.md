# Guidelines Plugin for Claude Code

A plugin that helps Claude Code learn from conversations by automatically extracting and applying guidelines.

## Features

- **Automatic Learning**: At the end of each conversation, guidelines are extracted and saved
- **Context-Aware Retrieval**: At the start of each prompt, relevant guidelines are injected
- **No Manual Configuration**: Hooks are automatically installed when the plugin is enabled

## Installation

```bash
# Load plugin for current session
claude --plugin-dir ./kaizen

# Or with absolute path
claude --plugin-dir /path/to/kaizen
```

See [INSTALL.md](INSTALL.md) for making it permanent, loading multiple plugins, and troubleshooting.

## How It Works

### Guideline Retrieval (UserPromptSubmit hook)

When you submit a prompt, the plugin:
1. Loads all stored guidelines from `.claude/guidelines.json`
2. Formats and injects them into the conversation context
3. Claude applies relevant guidelines to the current task

### Guideline Generation (Stop hook)

When a conversation ends, the plugin:
1. Analyzes the conversation trajectory
2. Extracts actionable guidelines from what worked/failed
3. Saves new guidelines to `.claude/guidelines.json`

## Skills Included

### `/guidelines:generator`

Manually invoke to extract guidelines from the current conversation:
- Analyzes task, steps taken, successes and failures
- Generates proactive guidelines (what to do, not what to avoid)
- Outputs JSON for storage

### `/guidelines:retrieval`

Manually invoke to retrieve and display stored guidelines.

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

After installation, run `claude plugin list` to confirm the plugin is enabled. See [INSTALL.md](INSTALL.md) for detailed verification steps.

## Plugin Structure

```text
kaizen/
├── .claude-plugin/
│   └── plugin.json              # Plugin manifest
├── skills/
│   ├── guideline-generator/
│   │   └── SKILL.md
│   └── guideline-retrieval/
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
