# Installation
## On Mac/Linux

### Bob Quick Start
```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform bob --mode lite
```

**What this installs:**
- Evolve Lite mode for IBM Bob
- Custom skills in `~/.bob/skills/` directory
- Custom mode configuration in `~/.bob/custom_modes.yaml`

**Post-Installation Steps:**
1. **Restart Bob** - Close and reopen IBM Bob IDE to load the new mode
2. **Switch to Evolve Lite mode** - In Bob, select "Evolve Lite" from the mode dropdown
3. **Verify installation** - The mode should appear in `~/.bob/custom_modes.yaml`

Next: [Hello World with IBM Bob](../examples/hello_world/bob.md)

### Claude Code Plugin Marketplace
```bash
claude plugin marketplace add AgentToolkit/altk-evolve
claude plugin install evolve-lite
```
Next: [Hello World with Claude Code](../examples/hello_world/claude.md)

### Download Install Script
```bash
# Latest (based on main)
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh -o install.sh && chmod +x install.sh

# Latest Stable Version
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/v1.0.5/platform-integrations/install.sh -o install.sh && chmod +x install.sh
```
### Install Script Usage
```bash
./install.sh install --platform {bob,claude,codex,all} --mode {lite,full} [--dry-run]
```

| Platform | Description |
|-----------|-------------|
| `all` | Install all platforms |
| `bob` | IBM Bob |
| `claude` | Claude Code |
| `codex` | Codex |

| Mode | Description |
|------|-------------|
| `lite` | Install only the core components. Some platforms only support lite. |
| `full` | Install all components including UI and CLI |

Use `--dry-run` to see what would be installed without making changes.

## Post-Installation

### For Bob Users

After running the install script:

1. **Restart Bob IDE** - The new mode will not appear until you restart
2. **Locate the mode** - Open Bob and look for "Evolve Lite" in the mode selector
3. **Verify files** - Check that `~/.bob/custom_modes.yaml` contains the Evolve Lite configuration
4. **Test the installation** - Try the [Hello World tutorial](../examples/hello_world/bob.md)

**Troubleshooting:**
- If the mode doesn't appear after restart, check `~/.bob/custom_modes.yaml` exists
- Ensure the install script completed without errors
- Try running the install script again (it's idempotent)

### For Claude Code Users

The Claude plugin system manages installation automatically. After installation:

1. Verify with: `claude plugin list`
2. The `evolve-lite` plugin should appear in the list

### For Codex Users

After installation:

1. Verify `plugins/evolve-lite/` exists in your project directory
2. Check `.agents/plugins/marketplace.json` contains the evolve-lite entry
3. Enable Codex hooks in `~/.codex/config.toml`:
   ```toml
   [features]
   codex_hooks = true
   ```
4. Restart Codex to load the plugin

## Common Issues

### "Instructions didn't work"

If you encounter issues:

1. **Check the working directory** - The install script installs to the current directory or `--dir` path
2. **Verify prerequisites** - Ensure Python 3.8+ is installed: `python3 --version`
3. **Run with dry-run first** - See what will be installed: `./install.sh install --platform bob --dry-run`
4. **Check for errors** - Look for error messages in the install output
5. **Restart the IDE** - Many platforms require a restart to load new configurations

### "No modes showing after install"

For Bob specifically:
- The mode is installed to `~/.bob/custom_modes.yaml`, not the project directory
- You must restart Bob IDE completely (not just reload)
- Check the file exists and contains the `evolve-lite` mode definition

### "What am I installing?"

The install script sets up:
- **Bob Mode**: A custom mode that integrates Evolve learning capabilities
- **Skills**: Pre-built commands for learning and recall operations
- **Configuration**: YAML/JSON files that Bob/Claude/Codex read on startup

You are NOT installing:
- A separate application
- System-wide changes (everything is in user directories)
- The full Evolve backend (that's optional with `--mode full`)
