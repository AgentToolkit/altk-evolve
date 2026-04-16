#!/usr/bin/env bash
# Evolve Platform Installer
# Installs Evolve Lite (and optionally Full) integrations for Bob, Claude Code, and Codex.
#
# Usage:
#   ./install.sh install [--platform bob|claude|codex|all] [--mode lite|full] [--dir DIR] [--dry-run]
#   ./install.sh uninstall [--platform bob|claude|codex|all] [--dir DIR] [--dry-run]
#   ./install.sh status [--dir DIR]
#
# Remote:
#   curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform bob
#
# Pinned version (SCRIPT_VERSION is substituted by the release process, so the
# script fetched from a tag already knows its own version — no env var needed):
#   curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/v1.2.0/platform-integrations/install.sh | bash

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────
EVOLVE_REPO="${EVOLVE_REPO:-AgentToolkit/altk-evolve}"
EVOLVE_DEBUG="${EVOLVE_DEBUG:-0}"

# Default to "main" so the installer always pulls the latest source.
# Callers can still pin a specific tag: EVOLVE_VERSION=v1.0.6 bash install.sh ...
SCRIPT_VERSION="v1.0.8"
EVOLVE_VERSION="${EVOLVE_VERSION:-${SCRIPT_VERSION}}"

# ─── Colours ──────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
  RED='\033[0;31m'; CYAN='\033[0;36m'; RESET='\033[0m'
else
  BOLD=''; GREEN=''; YELLOW=''; RED=''; CYAN=''; RESET=''
fi

info()    { echo -e "${CYAN}→${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

# ─── Python check ─────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  die "python3 is required but not found. Install Python 3.8+ and try again."
fi

PYTHON_OK=$(python3 -c "import sys; print(1 if sys.version_info >= (3,8) else 0)" 2>/dev/null || echo 0)
if [ "$PYTHON_OK" != "1" ]; then
  die "python3 >= 3.8 is required. Found: $(python3 --version 2>&1)"
fi

# ─── Source resolution ────────────────────────────────────────────────────────
# Resolve the directory containing this script (works for local runs).
# When piped from curl, BASH_SOURCE[0] is empty or "-", so we fall back to CWD.
if [ -n "${BASH_SOURCE[0]:-}" ] && [ "${BASH_SOURCE[0]}" != "-" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  SCRIPT_DIR="$(pwd)"
fi

SOURCE_DIR=""
TMPDIR_DOWNLOAD=""

resolve_source() {
  # The script lives inside platform-integrations/, so SOURCE_DIR is the parent.
  # Support two layouts:
  #   1. platform-integrations/install.sh  → SOURCE_DIR = parent of SCRIPT_DIR
  #   2. repo-root/install.sh              → SOURCE_DIR = SCRIPT_DIR (legacy / dev)
  local parent_dir
  parent_dir="$(dirname "${SCRIPT_DIR}")"

  if [ -d "${parent_dir}/platform-integrations" ]; then
    SOURCE_DIR="${parent_dir}"
    if [ "$EVOLVE_DEBUG" = "1" ]; then
      info "Using local source (parent): ${SOURCE_DIR}"
    fi
    return
  fi

  # Fallback: script is at repo root with platform-integrations/ alongside it
  if [ -d "${SCRIPT_DIR}/platform-integrations" ]; then
    SOURCE_DIR="${SCRIPT_DIR}"
    if [ "$EVOLVE_DEBUG" = "1" ]; then
      info "Using local source (same dir): ${SOURCE_DIR}"
    fi
    return
  fi

  # Remote: download tarball
  info "Downloading evolve source (${EVOLVE_VERSION})..."

  for cmd in curl tar; do
    command -v "$cmd" &>/dev/null || die "'$cmd' is required for remote install but not found."
  done

  TMPDIR_DOWNLOAD="$(mktemp -d)"
  trap 'rm -rf "$TMPDIR_DOWNLOAD"' EXIT

  local url
  if [ "$EVOLVE_VERSION" = "main" ] || [ "$EVOLVE_VERSION" = "latest" ]; then
    url="https://github.com/${EVOLVE_REPO}/archive/refs/heads/main.tar.gz"
  else
    url="https://github.com/${EVOLVE_REPO}/archive/refs/tags/${EVOLVE_VERSION}.tar.gz"
  fi

  if ! curl -fsSL "$url" | tar -xz -C "$TMPDIR_DOWNLOAD" --strip-components=1; then
    die "Failed to download or extract evolve from: ${url}"
  fi

  if [ ! -d "${TMPDIR_DOWNLOAD}/platform-integrations" ]; then
    die "Downloaded archive does not contain platform-integrations/. Check EVOLVE_REPO and EVOLVE_VERSION."
  fi

  SOURCE_DIR="$TMPDIR_DOWNLOAD"
  success "Downloaded evolve ${EVOLVE_VERSION}"
}

resolve_source

# ─── Hand off to Python ───────────────────────────────────────────────────────
# Pass SOURCE_DIR as argv[1], then all original CLI args.
# The heredoc uses single-quoted PYEOF so bash does not interpolate inside it.

exec python3 - "$SOURCE_DIR" "$@" <<'PYEOF'
import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────
SOURCE_DIR = sys.argv[1]
CLI_ARGS   = sys.argv[2:]

EVOLVE_DEBUG = os.environ.get("EVOLVE_DEBUG", "0") == "1"
DRY_RUN = False   # set to True by --dry-run flag; checked in all write primitives

BOB_SLUG    = "evolve-lite"
CLAUDE_PLUGIN = "evolve-lite"
CODEX_PLUGIN = "evolve-lite"


# ── Colour helpers ────────────────────────────────────────────────────────────
IS_TTY = sys.stdout.isatty()
def _c(code, text): return f"\033[{code}m{text}\033[0m" if IS_TTY else text
def info(msg):    print(_c("36", "→") + " " + msg)
def success(msg): print(_c("32", "✓") + " " + msg)
def warn(msg):    print(_c("33", "⚠") + " " + msg)
def error(msg):   print(_c("31", "✗") + " " + msg, file=sys.stderr)
def debug(msg):
    if EVOLVE_DEBUG: print(_c("35", "·") + " " + msg)
def dryrun(msg): print(_c("35", "[dry-run]") + " " + msg)


# ── File utilities ─────────────────────────────────────────────────────────────

def atomic_write_json(path, data):
    """Write JSON atomically via temp file + rename."""
    path = str(path)
    if DRY_RUN:
        dryrun(f"write JSON → {path}")
        debug(json.dumps(data, indent=2))
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".evolve.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    debug(f"Wrote JSON: {path}")


def atomic_write_text(path, text):
    """Write text atomically via temp file + rename."""
    path = str(path)
    if DRY_RUN:
        dryrun(f"write text → {path}")
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".evolve.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    debug(f"Wrote text: {path}")


def read_json(path):
    """Read a JSON file, return {} if not found. Back up and reset on parse error."""
    path = str(path)
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        bak = path + ".evolve.bak"
        warn(f"Could not parse {path} — backing up to {bak} and starting fresh.")
        shutil.copy2(path, bak)
        return {}


def _safe_copy2(src, dst):
    """Like shutil.copy2 but skips when src and dst are already the same file (hardlink/APFS clone)."""
    if os.path.exists(dst) and os.path.samefile(src, dst):
        debug(f"Skipping (same file): {src} → {dst}")
        return
    try:
        shutil.copy2(src, dst)
    except shutil.SameFileError:
        debug(f"Skipping (same file): {src} → {dst}")


def copy_tree(src, dst):
    """Idempotently copy a directory tree (Python 3.8+ dirs_exist_ok)."""
    src, dst = str(src), str(dst)
    if not os.path.isdir(src):
        raise FileNotFoundError(f"Source directory not found: {src}")
    if DRY_RUN:
        files = [os.path.relpath(os.path.join(r, f), src)
                 for r, _, fs in os.walk(src) for f in fs]
        dryrun(f"copy dir → {dst}/ ({len(files)} file(s): {', '.join(files[:5])}{'…' if len(files) > 5 else ''})")
        return
    os.makedirs(dst, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=_safe_copy2)
    debug(f"Copied {src} → {dst}")


def remove_dir(path):
    """Remove a directory if it exists."""
    path = str(path)
    if os.path.isdir(path):
        if DRY_RUN:
            dryrun(f"remove dir  → {path}")
            return True
        shutil.rmtree(path)
        debug(f"Removed dir: {path}")
        return True
    return False


def remove_file(path):
    """Remove a file if it exists."""
    path = str(path)
    if os.path.isfile(path):
        if DRY_RUN:
            dryrun(f"remove file → {path}")
            return True
        os.remove(path)
        debug(f"Removed file: {path}")
        return True
    return False


# ── JSON config helpers ────────────────────────────────────────────────────────

def merge_json_value(existing, desired):
    """Recursively merge JSON-like values, preserving unknown keys from existing objects."""
    if isinstance(existing, dict) and isinstance(desired, dict):
        merged = copy.deepcopy(existing)
        for key, desired_value in desired.items():
            merged[key] = merge_json_value(merged.get(key), desired_value)
        return merged
    return copy.deepcopy(desired)


def upsert_json_key(path, key_path: list, value):
    """Upsert a nested key into a JSON file. key_path = ['a', 'b', 'c'] → data['a']['b']['c'] = value."""
    data = read_json(path)
    cursor = data
    for key in key_path[:-1]:
        if not isinstance(cursor.get(key), dict):
            cursor[key] = {}
        cursor = cursor[key]
    cursor[key_path[-1]] = merge_json_value(cursor.get(key_path[-1]), value)
    atomic_write_json(path, data)


def remove_json_key(path, key_path: list):
    """Remove a nested key from a JSON file."""
    if not os.path.isfile(str(path)):
        return
    data = read_json(path)
    cursor = data
    for key in key_path[:-1]:
        if key not in cursor:
            return
        cursor = cursor[key]
    cursor.pop(key_path[-1], None)
    atomic_write_json(path, data)


def upsert_json_array_item(path, array_key: str, item: dict, id_key: str):
    """Upsert an item into a JSON array by identity key (e.g. 'slug')."""
    data = read_json(path)
    arr = data.setdefault(array_key, [])
    for i, existing in enumerate(arr):
        if existing.get(id_key) == item.get(id_key):
            arr[i] = merge_json_value(existing, item)
            break
    else:
        arr.append(copy.deepcopy(item))
    atomic_write_json(path, data)


def remove_json_array_item(path, array_key: str, id_key: str, id_val: str):
    """Remove an item from a JSON array by identity key."""
    if not os.path.isfile(str(path)):
        return
    data = read_json(path)
    arr = data.get(array_key, [])
    data[array_key] = [item for item in arr if item.get(id_key) != id_val]
    atomic_write_json(path, data)


def _default_codex_marketplace():
    return {
        "name": "evolve-local",
        "interface": {
            "displayName": "Evolve Local Plugins",
        },
        "plugins": [],
    }


def upsert_codex_marketplace_entry(path, item):
    """Upsert a Codex marketplace plugin entry by name."""
    data = read_json(path)
    if not data:
        data = _default_codex_marketplace()
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object.")

    interface = data.setdefault("interface", {})
    if not isinstance(interface, dict):
        interface = {}
        data["interface"] = interface
    data.setdefault("name", "evolve-local")
    interface.setdefault("displayName", "Evolve Local Plugins")

    plugins = data.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise ValueError(f"{path} field 'plugins' must be an array.")

    for index, existing in enumerate(plugins):
        if isinstance(existing, dict) and existing.get("name") == item.get("name"):
            plugins[index] = merge_json_value(existing, item)
            break
    else:
        plugins.append(copy.deepcopy(item))

    atomic_write_json(path, data)


def _codex_recall_hook_command():
    return (
        "sh -lc '"
        'd=\"$PWD\"; '
        "while :; do "
        'candidate=\"$d/plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py\"; '
        'if [ -f \"$candidate\" ]; then EVOLVE_DIR=\"$d/.evolve\" exec python3 \"$candidate\"; fi; '
        '[ \"$d\" = \"/\" ] && break; '
        'd=\"$(dirname \"$d\")\"; '
        "done; "
        "exit 1'"
    )


def _is_codex_recall_command(command):
    return isinstance(command, str) and "plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py" in command


def _codex_recall_hook():
    return {
        "type": "command",
        "command": _codex_recall_hook_command(),
        "statusMessage": "Loading Evolve guidance",
    }


def _codex_recall_hook_group():
    return {
        "matcher": "",
        "hooks": [_codex_recall_hook()],
    }


def _iter_group_hooks(group):
    hooks = group.get("hooks", [])
    if isinstance(hooks, list):
        return hooks
    if isinstance(hooks, dict):
        return hooks.values()
    return []


def _group_contains_codex_recall_command(group):
    return any(isinstance(hook, dict) and _is_codex_recall_command(hook.get("command")) for hook in _iter_group_hooks(group))


def _upsert_codex_recall_hook_into_group(group):
    updated_group = copy.deepcopy(group)
    recall_hook = _codex_recall_hook()
    hooks = updated_group.get("hooks")

    if isinstance(hooks, list):
        for index, existing_hook in enumerate(hooks):
            if isinstance(existing_hook, dict) and _is_codex_recall_command(existing_hook.get("command")):
                hooks[index] = merge_json_value(existing_hook, recall_hook)
                break
        else:
            hooks.append(copy.deepcopy(recall_hook))
        return updated_group

    if isinstance(hooks, dict):
        for key, existing_hook in hooks.items():
            if isinstance(existing_hook, dict) and _is_codex_recall_command(existing_hook.get("command")):
                hooks[key] = merge_json_value(existing_hook, recall_hook)
                break
        else:
            hooks["evolve-lite"] = copy.deepcopy(recall_hook)
        return updated_group

    updated_group["hooks"] = [copy.deepcopy(recall_hook)]
    return updated_group


def _remove_codex_recall_hook_from_group(group):
    updated_group = copy.deepcopy(group)
    hooks = updated_group.get("hooks")

    if isinstance(hooks, list):
        updated_group["hooks"] = [
            hook
            for hook in hooks
            if not (isinstance(hook, dict) and _is_codex_recall_command(hook.get("command")))
        ]
        return updated_group

    if isinstance(hooks, dict):
        updated_group["hooks"] = {
            key: hook
            for key, hook in hooks.items()
            if not (isinstance(hook, dict) and _is_codex_recall_command(hook.get("command")))
        }
        return updated_group

    return updated_group


def upsert_codex_user_prompt_hook(path, group):
    """Upsert the Evolve UserPromptSubmit hook into a Codex hooks.json file."""
    data = read_json(path)
    if not data:
        data = {"hooks": {}}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object.")

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks

    groups = hooks.setdefault("UserPromptSubmit", [])
    if not isinstance(groups, list):
        groups = []
        hooks["UserPromptSubmit"] = groups

    for index, existing in enumerate(groups):
        if isinstance(existing, dict) and _group_contains_codex_recall_command(existing):
            groups[index] = _upsert_codex_recall_hook_into_group(existing)
            break
    else:
        groups.append(copy.deepcopy(group))

    atomic_write_json(path, data)


def remove_codex_user_prompt_hook(path):
    """Remove the Evolve UserPromptSubmit hook from a Codex hooks.json file."""
    if not os.path.isfile(str(path)):
        return

    data = read_json(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return

    groups = hooks.get("UserPromptSubmit", [])
    if not isinstance(groups, list):
        return

    hooks["UserPromptSubmit"] = [
        _remove_codex_recall_hook_from_group(group)
        if isinstance(group, dict) and _group_contains_codex_recall_command(group)
        else group
        for group in groups
    ]
    if not hooks["UserPromptSubmit"]:
        hooks.pop("UserPromptSubmit", None)

    atomic_write_json(path, data)


# ── YAML helpers ───────────────────────────────────────────────────────────────

def _sentinel_start(slug): return f"# >>>evolve:{slug}<<<"
def _sentinel_end(slug):   return f"# <<<evolve:{slug}<<<"


def is_json_file(path):
    """Detect whether a file is JSON (vs YAML) by attempting to parse it."""
    try:
        with open(str(path)) as f:
            content = f.read().strip()
        json.loads(content)
        return True
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def merge_yaml_custom_mode(source_yaml_path, target_yaml_path, slug):
    """Merge a custom mode entry into a YAML custom_modes file using sentinel blocks."""
    source_yaml_path = str(source_yaml_path)
    target_yaml_path = str(target_yaml_path)

    with open(source_yaml_path) as f:
        source_text = f.read()

    # Extract the mode block lines from under the top-level "customModes:" key,
    # stripping one level of indent so the block is ready to re-indent as needed.
    mode_lines = []
    in_modes = False
    for line in source_text.splitlines():
        if line.strip() == "customModes:":
            in_modes = True
            continue
        if in_modes:
            mode_lines.append(line[2:] if line.startswith("  ") else line)

    mode_block = "\n".join(mode_lines).strip()

    start = _sentinel_start(slug)
    end   = _sentinel_end(slug)
    block = f"\n{start}\n  {mode_block.replace(chr(10), chr(10) + '  ')}\n{end}\n"

    try:
        with open(target_yaml_path) as f:
            existing = f.read()
    except FileNotFoundError:
        existing = "customModes:\n"

    # Ensure proper YAML structure if file is empty or doesn't contain customModes
    if not existing.strip() or "customModes:" not in existing:
        existing = "customModes:\n"

    if start in existing:
        pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
        new_content = pattern.sub(block.strip(), existing)
    else:
        new_content = existing.rstrip() + block

    atomic_write_text(target_yaml_path, new_content)
    debug(f"YAML merge (sentinel): {target_yaml_path}")


def remove_yaml_custom_mode(target_yaml_path, slug):
    """Remove a sentinel-wrapped custom mode entry from a YAML file."""
    target_yaml_path = str(target_yaml_path)
    if not os.path.isfile(target_yaml_path):
        return

    with open(target_yaml_path) as f:
        text = f.read()
    start = _sentinel_start(slug)
    end   = _sentinel_end(slug)
    pattern = re.compile(
        r"\n?" + re.escape(start) + r".*?" + re.escape(end) + r"\n?",
        re.DOTALL
    )
    atomic_write_text(target_yaml_path, pattern.sub("", text))



# ── Platform detection ─────────────────────────────────────────────────────────

def detect_platforms(target_dir):
    target = Path(target_dir)
    return {
        "bob": (
            shutil.which("bob") is not None or
            (target / ".bob").is_dir()
        ),
        "claude": (
            shutil.which("claude") is not None or
            (target / ".claude").is_dir()
        ),
        "codex": (
            shutil.which("codex") is not None or
            (target / ".codex").is_dir() or
            (target / ".agents" / "plugins" / "marketplace.json").is_file()
        ),
    }


def interactive_select(detected):
    """Prompt user to choose platforms. Returns list of selected platform names."""
    print()
    print("Detected platforms:")
    options = list(detected.keys())
    for i, name in enumerate(options, 1):
        indicator = "\033[32m✓\033[0m" if detected[name] else "·"
        note = "detected" if detected[name] else "not detected"
        print(f"  {i}. {name} ({note}) {indicator}")
    print(f"  {len(options)+1}. all")
    print(f"  0. cancel")
    print()

    raw = input("Install which platform(s)? Enter number(s) separated by space: ").strip()
    if not raw or raw == "0":
        print("Cancelled.")
        sys.exit(0)

    selected = []
    for part in raw.split():
        try:
            n = int(part)
        except ValueError:
            continue
        if n == len(options) + 1:
            return list(options)
        elif 1 <= n <= len(options):
            selected.append(options[n - 1])

    if not selected:
        print("No valid selection. Cancelled.")
        sys.exit(0)

    return selected


# ── Bob installer ─────────────────────────────────────────────────────────────

def install_bob(source_dir, target_dir, mode="lite"):
    bob_source_lite = Path(source_dir) / "platform-integrations" / "bob" / "evolve-lite"
    bob_target = Path(target_dir) / ".bob"

    info(f"Installing Bob ({mode} mode) → {bob_target}")

    if mode == "lite":
        # Shared lib (entity_io) — single source of truth lives in the Claude plugin
        shared_lib = Path(source_dir) / "platform-integrations" / "claude" / "plugins" / "evolve-lite" / "lib"
        if not shared_lib.is_dir():
            error(f"Shared lib not found: {shared_lib} — is the Claude plugin present in the source tree?")
            sys.exit(1)
        copy_tree(shared_lib, bob_target / "evolve-lib")
        success("Copied Bob lib")

        # Skills
        copy_tree(bob_source_lite / "skills" / "evolve-lite:learn",  bob_target / "skills" / "evolve-lite:learn")
        copy_tree(bob_source_lite / "skills" / "evolve-lite:recall", bob_target / "skills" / "evolve-lite:recall")
        success("Copied Bob skills")

        # Commands
        copy_tree(bob_source_lite / "commands", bob_target / "commands")
        success("Copied Bob commands")

        # custom_modes.yaml
        source_modes_yaml = bob_source_lite / "custom_modes.yaml"
        target_modes_yaml = bob_target / "custom_modes.yaml"
        merge_yaml_custom_mode(source_modes_yaml, target_modes_yaml, BOB_SLUG)
        success(f"Merged custom mode '{BOB_SLUG}' into {target_modes_yaml}")

    elif mode == "full":
        # Full mode: mcp.json and custom_modes.yaml
        bob_source_full = Path(source_dir) / "platform-integrations" / "bob" / "evolve-full"
        
        # MCP configuration
        mcp_source = bob_source_full / "mcp.json"
        if not mcp_source.exists():
            error(f"Source MCP config not found: {mcp_source}")
            sys.exit(1)
        mcp_target = bob_target / "mcp.json"
        with open(mcp_source) as f:
            mcp_data = json.load(f)
        evolve_server = mcp_data["mcpServers"]["evolve"]
        upsert_json_key(mcp_target, ["mcpServers", "evolve"], evolve_server)
        success(f"Upserted MCP server config in {mcp_target}")
        
        # custom_modes.yaml
        source_modes_yaml = bob_source_full / "custom_modes.yaml"
        target_modes_yaml = bob_target / "custom_modes.yaml"
        merge_yaml_custom_mode(source_modes_yaml, target_modes_yaml, "Evolve")
        success(f"Merged custom mode 'Evolve' into {target_modes_yaml}")

    success("Bob installation complete")


def uninstall_bob(target_dir, mode="full"):
    bob_target = Path(target_dir) / ".bob"
    info(f"Uninstalling Bob from {bob_target}")

    remove_dir(bob_target / "evolve-lib")
    remove_dir(bob_target / "skills" / "evolve-lite:learn")
    remove_dir(bob_target / "skills" / "evolve-lite:recall")
    remove_file(bob_target / "commands" / "evolve-lite:learn.md")
    remove_file(bob_target / "commands" / "evolve-lite:recall.md")
    # Remove both lite and full mode custom modes
    remove_yaml_custom_mode(bob_target / "custom_modes.yaml", BOB_SLUG)  # evolve-lite
    remove_yaml_custom_mode(bob_target / "custom_modes.yaml", "Evolve")  # full mode
    remove_json_key(bob_target / "mcp.json", ["mcpServers", "evolve"])

    success("Bob uninstall complete")


def status_bob(target_dir):
    bob_target = Path(target_dir) / ".bob"
    print(f"  Bob (.bob/):")
    print(f"    evolve-lib/entity_io  : {'✓' if (bob_target / 'evolve-lib' / 'entity_io.py').is_file() else '✗'}")
    print(f"    skills/evolve-lite:learn  : {'✓' if (bob_target / 'skills' / 'evolve-lite:learn').is_dir() else '✗'}")
    print(f"    skills/evolve-lite:recall : {'✓' if (bob_target / 'skills' / 'evolve-lite:recall').is_dir() else '✗'}")
    print(f"    commands/            : {'✓' if (bob_target / 'commands' / 'evolve-lite:learn.md').is_file() else '✗'}")
    print(f"    custom_modes.yaml    : {'✓' if (bob_target / 'custom_modes.yaml').is_file() else '✗'}")

    mcp_path = bob_target / "mcp.json"
    has_mcp = False
    if mcp_path.is_file():
        mcp = read_json(mcp_path)
        has_mcp = "evolve" in mcp.get("mcpServers", {})
    print(f"    mcp.json (full mode) : {'✓' if has_mcp else '✗'}")



# ── Claude installer ──────────────────────────────────────────────────────────

def install_claude(source_dir, target_dir):
    plugin_source = Path(source_dir) / "platform-integrations" / "claude" / "plugins" / "evolve-lite"
    info(f"Installing Claude plugin from {plugin_source}")

    claude = shutil.which("claude")
    if claude:
        if DRY_RUN:
            dryrun(f"run: claude plugin install {plugin_source.resolve()}")
            return
        try:
            result = subprocess.run(
                [claude, "plugin", "install", str(plugin_source.resolve())],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                success("Claude plugin installed via CLI")
                if result.stdout.strip():
                    print(f"    {result.stdout.strip()}")
                return
            else:
                warn(f"claude plugin install exited with code {result.returncode}")
                if result.stderr.strip():
                    warn(f"    {result.stderr.strip()}")
        except Exception as e:
            warn(f"claude plugin install failed: {e}")

    # Fallback: manual instructions
    abs_path = plugin_source.resolve()
    warn("Could not install Claude plugin automatically. To install manually, run:")
    print()
    print(f"    claude --plugin-dir {abs_path}")
    print()
    print("  Or add this to your Claude startup command.")


def uninstall_claude(target_dir):
    info("Uninstalling Claude plugin")
    claude = shutil.which("claude")
    if claude:
        if DRY_RUN:
            dryrun(f"run: claude plugin uninstall {CLAUDE_PLUGIN}")
            return
        try:
            result = subprocess.run(
                [claude, "plugin", "uninstall", CLAUDE_PLUGIN],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                success("Claude plugin uninstalled via CLI")
                return
            warn(f"claude plugin uninstall exited with code {result.returncode}: {result.stderr.strip()}")
        except Exception as e:
            warn(f"claude plugin uninstall failed: {e}")

    warn("Could not uninstall Claude plugin automatically.")
    warn(f"Run manually: claude plugin uninstall {CLAUDE_PLUGIN}")


def status_claude(target_dir):
    print(f"  Claude:")
    claude = shutil.which("claude")
    if not claude:
        print(f"    claude CLI          : ✗ (not found on PATH)")
        return
    print(f"    claude CLI          : ✓")
    try:
        result = subprocess.run(
            [claude, "plugin", "list"],
            capture_output=True, text=True
        )
        installed = CLAUDE_PLUGIN in result.stdout
        print(f"    evolve-lite plugin  : {'✓' if installed else '✗ (not installed)'}")
    except Exception:
        print(f"    evolve-lite plugin  : ? (could not query)")


# ── Codex installer ───────────────────────────────────────────────────────────

def install_codex(source_dir, target_dir):
    plugin_source = Path(source_dir) / "platform-integrations" / "codex" / "plugins" / CODEX_PLUGIN
    plugin_target = Path(target_dir) / "plugins" / CODEX_PLUGIN
    info(f"Installing Codex → {plugin_target}")

    copy_tree(plugin_source, plugin_target)
    success("Copied Codex plugin")

    shared_lib = Path(source_dir) / "platform-integrations" / "claude" / "plugins" / "evolve-lite" / "lib"
    if not shared_lib.is_dir():
        error(f"Shared lib not found: {shared_lib} — is the Claude plugin present in the source tree?")
        sys.exit(1)
    copy_tree(shared_lib, plugin_target / "lib")
    success("Copied Codex lib")

    marketplace_target = Path(target_dir) / ".agents" / "plugins" / "marketplace.json"
    upsert_codex_marketplace_entry(
        marketplace_target,
        {
            "name": CODEX_PLUGIN,
            "source": {
                "source": "local",
                "path": f"./plugins/{CODEX_PLUGIN}",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        },
    )
    success(f"Upserted Codex marketplace entry in {marketplace_target}")

    hooks_target = Path(target_dir) / ".codex" / "hooks.json"
    upsert_codex_user_prompt_hook(hooks_target, _codex_recall_hook_group())
    success(f"Upserted Codex UserPromptSubmit hook in {hooks_target}")
    warn("Automatic Codex recall requires hooks to be enabled in ~/.codex/config.toml:")
    print("      [features]")
    print("      codex_hooks = true")
    info("If you do not want to enable Codex hooks, invoke the installed evolve-lite:recall skill manually.")

    success("Codex installation complete")


def uninstall_codex(target_dir):
    info(f"Uninstalling Codex from {target_dir}")

    remove_dir(Path(target_dir) / "plugins" / CODEX_PLUGIN)
    remove_json_array_item(Path(target_dir) / ".agents" / "plugins" / "marketplace.json", "plugins", "name", CODEX_PLUGIN)
    remove_codex_user_prompt_hook(Path(target_dir) / ".codex" / "hooks.json")

    success("Codex uninstall complete")


def status_codex(target_dir):
    plugin_dir = Path(target_dir) / "plugins" / CODEX_PLUGIN
    print("  Codex:")
    print(f"    plugins/evolve-lite       : {'✓' if plugin_dir.is_dir() else '✗'}")
    print(f"    lib/entity_io.py          : {'✓' if (plugin_dir / 'lib' / 'entity_io.py').is_file() else '✗'}")
    print(f"    skills/learn              : {'✓' if (plugin_dir / 'skills' / 'learn').is_dir() else '✗'}")
    print(f"    skills/recall             : {'✓' if (plugin_dir / 'skills' / 'recall').is_dir() else '✗'}")

    marketplace_path = Path(target_dir) / ".agents" / "plugins" / "marketplace.json"
    marketplace_present = False
    if marketplace_path.is_file():
        data = read_json(marketplace_path)
        marketplace_present = any(entry.get("name") == CODEX_PLUGIN for entry in data.get("plugins", []))
    print(f"    marketplace.json entry    : {'✓' if marketplace_present else '✗'}")

    hooks_path = Path(target_dir) / ".codex" / "hooks.json"
    hook_present = False
    if hooks_path.is_file():
        data = read_json(hooks_path)
        hook_groups = data.get("hooks", {}).get("UserPromptSubmit", [])
        hook_present = any(
            isinstance(group, dict) and _group_contains_codex_recall_command(group)
            for group in hook_groups
        )
    print(f"    .codex/hooks.json entry   : {'✓' if hook_present else '✗'}")


# ── Dispatch ──────────────────────────────────────────────────────────────────

def cmd_install(args):
    global DRY_RUN
    DRY_RUN = args.dry_run
    target_dir = os.path.abspath(args.dir)

    # Resolve platforms
    if args.platform == "all":
        platforms = ["bob", "claude", "codex"]
    elif args.platform:
        platforms = [args.platform]
    else:
        detected = detect_platforms(target_dir)
        platforms = interactive_select(detected)

    print()
    if DRY_RUN:
        info(_c("35", "DRY RUN — no files will be written or deleted"))
    info(f"Target directory: {target_dir}")
    info(f"Platforms: {', '.join(platforms)}")
    if "bob" in platforms:
        info(f"Bob mode: {args.mode}")
    print()

    errors = []
    for platform in platforms:
        try:
            if platform == "bob":
                install_bob(SOURCE_DIR, target_dir, mode=args.mode)
            elif platform == "claude":
                install_claude(SOURCE_DIR, target_dir)
            elif platform == "codex":
                install_codex(SOURCE_DIR, target_dir)
        except Exception as e:
            error(f"Failed to install {platform}: {e}")
            if EVOLVE_DEBUG:
                import traceback; traceback.print_exc()
            errors.append(platform)

    print()
    if errors:
        warn(f"Installation completed with errors on: {', '.join(errors)}")
        sys.exit(1)
    else:
        if DRY_RUN:
            success("Dry run complete — no changes were made.")
        else:
            success("All installations complete.")


def cmd_uninstall(args):
    global DRY_RUN
    DRY_RUN = args.dry_run
    target_dir = os.path.abspath(args.dir)

    if DRY_RUN:
        print()
        info(_c("35", "DRY RUN — no files will be written or deleted"))

    if args.platform == "all":
        platforms = ["bob", "claude", "codex"]
    elif args.platform:
        platforms = [args.platform]
    else:
        detected = detect_platforms(target_dir)
        platforms = interactive_select(detected)

    print()
    errors = []
    for platform in platforms:
        try:
            if platform == "bob":
                uninstall_bob(target_dir)
            elif platform == "claude":
                uninstall_claude(target_dir)
            elif platform == "codex":
                uninstall_codex(target_dir)
        except Exception as e:
            error(f"Failed to uninstall {platform}: {e}")
            errors.append(platform)

    print()
    if errors:
        warn(f"Uninstall completed with errors on: {', '.join(errors)}")
        sys.exit(1)
    else:
        if DRY_RUN:
            success("Dry run complete — no changes were made.")
        else:
            success("Uninstall complete.")


def cmd_status(args):
    target_dir = os.path.abspath(args.dir)
    print()
    print(f"Evolve installation status in: {target_dir}")
    print()
    status_bob(target_dir)
    print()
    status_claude(target_dir)
    print()
    status_codex(target_dir)
    print()


# ── argparse ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="install.sh",
        description="Install Evolve integrations for Bob, Claude Code, and Codex.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # install
    p_install = sub.add_parser("install", help="Install Evolve into the current project")
    p_install.add_argument(
        "--platform", choices=["bob", "claude", "codex", "all"], default=None,
        help="Platform to install (default: auto-detect and prompt)",
    )
    p_install.add_argument(
        "--mode", choices=["lite", "full"], default="lite",
        help="Installation mode for Bob (default: lite)",
    )
    p_install.add_argument(
        "--dir", default=os.getcwd(),
        help="Target project directory (default: current working directory)",
    )
    p_install.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Show what would be done without making any changes",
    )

    # uninstall
    p_uninstall = sub.add_parser("uninstall", help="Remove Evolve from the current project")
    p_uninstall.add_argument(
        "--platform", choices=["bob", "claude", "codex", "all"], default=None,
        help="Platform to uninstall (default: prompt)",
    )
    p_uninstall.add_argument(
        "--dir", default=os.getcwd(),
        help="Target project directory (default: current working directory)",
    )
    p_uninstall.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Show what would be done without making any changes",
    )

    # status
    p_status = sub.add_parser("status", help="Show what is currently installed")
    p_status.add_argument(
        "--dir", default=os.getcwd(),
        help="Target project directory (default: current working directory)",
    )

    args = parser.parse_args(CLI_ARGS)

    if args.command == "install":
        cmd_install(args)
    elif args.command == "uninstall":
        cmd_uninstall(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()

PYEOF
