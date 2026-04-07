# Evolve Platform Installer — Specification

## Overview

`install.sh` is a single-file bash/Python hybrid installer that sets up Evolve integrations
into a user's project directory for one or more supported platforms: **Bob**, **Claude**, and **Codex**.

It is designed to be run:
- Locally from within the evolve repo: `./install.sh install`
- Remotely via curl: `curl -fsSL https://raw.githubusercontent.com/.../install.sh | bash`

---

## Source Resolution

The installer needs the `platform-integrations/` source files. It resolves them in this order:

1. **Local mode** (default when run from repo): detects that `platform-integrations/` exists
   relative to the script, uses it directly. No network required.
2. **Remote mode**: downloads a release tarball from GitHub using `curl | tar`, extracts to
   a temp directory, then runs the install. Cleaned up on exit.

The download URL format:
```
https://github.com/${EVOLVE_REPO}/archive/refs/heads/main.tar.gz
```
Or a pinned version:
```
https://github.com/${EVOLVE_REPO}/archive/refs/tags/v${VERSION}.tar.gz
```

`EVOLVE_REPO` defaults to `AgentToolkit/altk-evolve` and can be overridden by env var.
`EVOLVE_VERSION` defaults to `SCRIPT_VERSION`, a constant embedded in the script
that the release process substitutes with the actual tag (e.g. `v1.2.0`). This means
a script fetched from a tag URL already knows which tarball to download — callers
never need to set `EVOLVE_VERSION` manually.

---

## CLI Interface

```
install.sh <command> [options]

Commands:
  install    Install evolve into the current project directory
  uninstall  Remove evolve from the current project directory
  status     Show what is currently installed

install options:
  --platform {bob,claude,codex,all}   Platform to install (default: auto-detect + prompt)
  --mode     {lite,full}            Installation mode for bob (default: lite)
  --dir      DIR                    Target project directory (default: current working dir)
  --dry-run                         Preview changes without modifying files

uninstall options:
  --platform {bob,claude,codex,all}   Platform to uninstall (default: prompt)
  --dir      DIR                    Target project directory (default: current working dir)
  --dry-run                         Preview changes without modifying files
```

---

## Platform Detection

Detection checks in order (any match = platform considered available):

| Platform | Detection signals |
|----------|-------------------|
| bob      | `.bob/` dir exists in target dir, OR `bob` on PATH |
| claude   | `.claude/` dir exists in target dir, OR `claude` on PATH |
| codex    | `.codex/` dir exists in target dir, OR `.agents/plugins/marketplace.json` exists, OR `codex` on PATH |

If no `--platform` flag is given, the script runs interactively: shows detected platforms,
lets the user pick one, multiple, or all.

---

## Install Actions

### Bob — Lite Mode

Source: `platform-integrations/bob/evolve-lite/`
Target: `.bob/` in project directory

1. Copy `skills/evolve-lite:learn/` → `.bob/skills/evolve-lite:learn/`  (merge, idempotent)
2. Copy `skills/evolve-lite:recall/` → `.bob/skills/evolve-lite:recall/`  (merge, idempotent)
3. Copy `commands/` → `.bob/commands/`  (merge, idempotent)
4. Merge `custom_modes.yaml` → `.bob/custom_modes.yaml`  (sentinel block, see YAML Strategy)

### Bob — Full Mode

All of lite mode, plus:

5. Read `platform-integrations/bob/evolve-full/mcp.json`
6. Upsert key `mcpServers.evolve` into `.bob/mcp.json`  (JSON key upsert, see JSON Strategy)

### Claude — Lite Mode

Source: `platform-integrations/claude/plugins/evolve-lite/`

1. Attempt `claude plugin install <abs-path-to-plugin-dir>` via subprocess
2. If claude CLI not found or command fails, print clear manual instructions:
   ```
   claude --plugin-dir /path/to/platform-integrations/claude/plugins/evolve-lite
   ```
3. No file-system fallback for Claude (plugin system manages its own state)

### Codex — Lite Mode

Source: `platform-integrations/codex/plugins/evolve-lite/`
Target: project directory

1. Copy `platform-integrations/codex/plugins/evolve-lite/` → `plugins/evolve-lite/` in the target project
2. Copy shared lib from `platform-integrations/claude/plugins/evolve-lite/lib/` → `plugins/evolve-lite/lib/`
3. Upsert plugin entry `evolve-lite` into `.agents/plugins/marketplace.json`
4. Upsert a `UserPromptSubmit` hook into `.codex/hooks.json` that runs the Evolve recall helper script by walking upward from the current working directory until it finds `plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py` (does not require `git`)
5. Print post-install guidance that automatic recall requires `~/.codex/config.toml` to include:
   ```toml
   [features]
   codex_hooks = true
   ```
6. Print a manual fallback note that users can invoke `evolve-lite:recall` directly if they do not want to enable Codex hooks

Codex is currently implemented only in lite mode. Full mode is reserved for future MCP-backed work.

---

## Uninstall Actions

### Bob
1. Remove `.bob/skills/evolve-lite:learn/`
2. Remove `.bob/skills/evolve-lite:recall/`
3. Remove `.bob/commands/evolve-lite:learn.md` and `evolve-lite:recall.md`
4. Remove sentinel block for `evolve-lite` from `.bob/custom_modes.yaml`
5. (Full mode) Remove `mcpServers.evolve` key from `.bob/mcp.json`

### Claude
1. Attempt `claude plugin uninstall evolve-lite` via subprocess
2. If that fails, print manual instructions

### Codex
1. Remove `plugins/evolve-lite/`
2. Remove the `evolve-lite` entry from `.agents/plugins/marketplace.json`
3. Remove the Evolve `UserPromptSubmit` hook from `.codex/hooks.json`

---

## File Operation Strategies

### JSON Strategy (mcp.json, marketplace.json, hooks.json)

All JSON writes use atomic read-modify-write:
1. Read existing file (or start with `{}` if not found)
2. Modify the target key/array in memory
3. Write to `<path>.evolve.tmp`
4. `os.replace(tmp, path)` — atomic on POSIX

**Key upsert** (`mcpServers.evolve`, `hooks.UserPromptSubmit` scaffolding): navigate nested keys via `dict.setdefault`, merge matching dict values in place, and only replace scalar/list leaves.

**Array upsert** (`marketplace.json` `plugins`): iterate array, find item where the identity key matches,
merge matching dict items in place; append if not found.

**Array remove**: filter array by `item["slug"] != target_slug`, write back.

### YAML Strategy (custom_modes.yaml)

YAML files use sentinel comment blocks:

```yaml
customModes:
  - slug: other-mode
    ...
# >>>evolve:evolve-lite<<<
  - slug: evolve-lite
    name: Evolve Lite
    ...
# <<<evolve:evolve-lite<<<
```

**Install**: check if sentinel `# >>>evolve:evolve-lite<<<` exists in file. If yes, replace the block
between sentinels. If no, append sentinel block to end of file.

**Uninstall**: find sentinel start and end lines, remove all lines between them (inclusive).


---

## Idempotency

All operations are safe to run multiple times:
- Directory copies use `shutil.copytree(..., dirs_exist_ok=True)`
- JSON writes upsert (replace-if-exists, insert-if-not)
- YAML writes check for sentinel before appending
- Claude plugin install is idempotent by the Claude CLI itself
- Codex marketplace and hook writes merge matching Evolve entries and preserve user-owned entries

---

## Dependencies

| Dependency | Required for | Notes |
|------------|-------------|-------|
| `python3 >= 3.8` | Everything | Checked at startup; clear error if missing |
| `curl` | Remote mode only | Required to download source tarball |
| `tar` | Remote mode only | Required to extract tarball |
| `claude` CLI | Claude install | Falls back to manual instructions if absent |

No pip packages are required. The script uses only Python stdlib.

---

## Error Handling

- Python < 3.8: print error with install instructions, exit 1
- `curl` or `tar` not found in remote mode: print error, exit 1
- JSON parse errors on existing config files: back up the file as `<file>.evolve.bak`, start fresh
- File permission errors: print specific error and path, exit 1
- Partial install failure: operations already completed are not rolled back (they are idempotent
  anyway); remaining operations are skipped with a summary of what succeeded and what failed

---

## Logging

- Normal output: plain text with `✓` / `✗` / `→` indicators
- `EVOLVE_DEBUG=1` env var: enables verbose output with detailed file operations

---

## Remote Install Example

```bash
# Latest main
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash

# Pinned version — the script fetched from the tag already knows its own version
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/v1.2.0/platform-integrations/install.sh | bash

# Non-interactive, specific platform
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | \
  bash -s -- install --platform bob
```

## Local Install Example

```bash
# From within the evolve repo
./platform-integrations/install.sh install              # interactive
./platform-integrations/install.sh install --platform bob --mode full
./platform-integrations/install.sh install --platform all
./platform-integrations/install.sh status
./platform-integrations/install.sh uninstall --platform bob
```
