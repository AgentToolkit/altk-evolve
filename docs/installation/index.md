# Installation
## On Mac/Linux

### Bob Quick Start
```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform bob --mode lite
```
### Download Install Script
```bash
# Latest (based on main)
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh -o install.sh && chmod +x install.sh

# Latest Stable Version
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/v1.0.5/platform-integrations/install.sh -o install.sh && chmod +x install.sh
```
### Install Script Usage
```bash
./install.sh install --platform {bob,roo,claude,all} --mode {lite,full} [--dry-run]
```

| Platform | Description |
|-----------|-------------|
| `all` | Install all platforms |
| `bob` | IBM Bob |
| `roo` | Roo Code |
| `claude` | Claude Code |

| Mode | Description |
|------|-------------|
| `lite` | Install only the core components. Some platforms only support lite. |
| `full` | Install all components including UI and CLI |

Use `--dry-run` to see what would be installed without making changes.
