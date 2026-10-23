# Installation
## On Mac/Linux

### Bob Quick Start
```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform bob --mode lite
```
Next: [Hello World with IBM Bob](../examples/hello_world/bob.md)

### Claude Code Quick Start
Run the installer from a clone, for the project you want Evolve in. Keep the clone: the plugin is read from it.
```bash
git clone --depth 1 https://github.com/AgentToolkit/altk-evolve.git ~/altk-evolve
~/altk-evolve/platform-integrations/install.sh install --platform claude --dir /path/to/your/project
```
Besides installing the `evolve-lite` plugin, the installer adds `.evolve/EVOLVE.md` and its import line to the project's `CLAUDE.md`, and installs the scripts that file names. Installing only the plugin (`claude plugin install`) skips all of that, so `/evolve-lite:doctor` reports `NOT_INSTALLED`. See [Evolve Lite for Claude Code](../integrations/claude/evolve-lite.md).

!!! warning "Installing without a clone"
    An installer without the fix for [#387](https://github.com/AgentToolkit/altk-evolve/issues/387), run without a clone around it (the piped `curl … | bash` form, or a downloaded `install.sh`), registers its temporary download directory as the plugin's marketplace and deletes that directory on exit. The plugin then fails to load: `claude plugin list` shows `✘ failed to load` and `cache-miss`. If you see that, reinstall from a clone as above.

Next: [Hello World with Claude Code](../examples/hello_world/claude.md)

### Hermes Quick Start
```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform hermes
hermes config set memory.provider evolve
```
Next: [Hermes Memory Provider](../integrations/hermes.md)

### Download Install Script
```bash
# Latest (based on main)
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh -o install.sh && chmod +x install.sh

# Latest Stable Version
curl -fsSL https://github.com/AgentToolkit/altk-evolve/releases/latest/download/install.sh -o install.sh && chmod +x install.sh
```
### Install Script Usage
```bash
./install.sh install --platform {bob,claude,claw-code,codex,hermes,all} --mode {lite,full} [--dry-run]
```

| Platform | Description |
|-----------|-------------|
| `all` | Install all platforms |
| `bob` | IBM Bob |
| `claude` | Claude Code |
| `claw-code` | Claw Code |
| `codex` | Codex |
| `hermes` | [Hermes](../integrations/hermes.md) (installs a memory provider into `$HERMES_HOME`; ignores `--dir`) |

| Mode | Description |
|------|-------------|
| `lite` | Install only the core components. Some platforms only support lite. |
| `full` | Install all components including UI and CLI |

Use `--dry-run` to see what would be installed without making changes.
