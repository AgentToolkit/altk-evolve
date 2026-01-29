# Installation Guide

## Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed
- Python 3.8+ available in PATH

## Option 1: Load Local Plugin (Development)

Use `--plugin-dir` to load the plugin for a session:

```bash
# Load plugin when starting Claude Code (absolute path)
claude --plugin-dir /path/to/kaizen

# Or use a relative path from current directory
claude --plugin-dir ./kaizen
```

**Note:** The plugin is loaded **for that session only**.

### Loading Multiple Plugins

```bash
claude --plugin-dir ./plugin1 --plugin-dir ./plugin2
```

### Making It Permanent

Add an alias to your shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
alias claude='claude --plugin-dir /path/to/kaizen'
```

Then reload your shell:

```bash
source ~/.zshrc  # or source ~/.bashrc
```

## Option 2: Install from Marketplace (when published)

```bash
claude plugin install kaizen
```

## Validate Plugin

Before using, validate the plugin manifest:

```bash
claude plugin validate /path/to/kaizen
# Should output: ✔ Validation passed
```

## Initialize Guidelines File (Optional)

```bash
mkdir -p .claude
echo '{"guidelines": []}' > .claude/guidelines.json
```

## Verification

After loading the plugin, verify it's working:

1. **Test hook execution:**
   ```bash
   # Start Claude Code with the plugin
   claude --plugin-dir ./kaizen

   # Send any prompt, then check the debug log (in another terminal)
   cat /tmp/guidelines-plugin.log
   # Should show: "[retrieve] Script started" with timestamp
   ```

2. **Test guideline storage:**
   ```bash
   # After ending a conversation where guidelines were generated
   cat .claude/guidelines.json
   # Should contain the extracted guidelines
   ```

3. **Test skills manually:**
   ```bash
   # In a Claude Code session with the plugin loaded, invoke:
   /guidelines:generator
   /guidelines:retrieval
   ```

## Troubleshooting

### Plugin validation fails

Run `claude plugin validate ./kaizen` to see specific errors.

### Hooks not firing

1. Verify the plugin is loaded with `--plugin-dir`
2. Verify Python is in PATH: `which python3`
3. Check script permissions: `ls -la kaizen/scripts/`
4. Check debug log: `cat /tmp/guidelines-plugin.log`

### Guidelines not saving

1. Verify `.claude/` directory exists in your project
2. Check write permissions on the directory
3. Review `/tmp/guidelines-plugin.log` for error messages
