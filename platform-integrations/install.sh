#!/usr/bin/env bash
# Kaizen Platform Installer
# Installs Kaizen Lite (and optionally Full) integrations for Bob, Roo, and Claude Code.
#
# Usage:
#   ./install.sh install [--platform bob|roo|claude|all] [--mode lite|full] [--dir DIR] [--dry-run]
#   ./install.sh uninstall [--platform bob|roo|claude|all] [--dir DIR] [--dry-run]
#   ./install.sh status [--dir DIR]
#
# Remote:
#   curl -fsSL https://raw.githubusercontent.com/AgentToolkit/kaizen/main/platform-integrations/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/AgentToolkit/kaizen/main/platform-integrations/install.sh | bash -s -- install --platform roo
#
# Pinned version (SCRIPT_VERSION is substituted by the release process, so the
# script fetched from a tag already knows its own version — no env var needed):
#   curl -fsSL https://raw.githubusercontent.com/AgentToolkit/kaizen/v1.2.0/platform-integrations/install.sh | bash

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────
KAIZEN_REPO="${KAIZEN_REPO:-AgentToolkit/kaizen}"
KAIZEN_DEBUG="${KAIZEN_DEBUG:-0}"

# SCRIPT_VERSION is substituted by the release process (e.g. sed to "v1.2.0").
# This means a script fetched from a tag URL already knows its own version,
# so callers never need to set KAIZEN_VERSION manually.
SCRIPT_VERSION="main"
KAIZEN_VERSION="${KAIZEN_VERSION:-${SCRIPT_VERSION}}"

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
    if [ "$KAIZEN_DEBUG" = "1" ]; then
      info "Using local source (parent): ${SOURCE_DIR}"
    fi
    return
  fi

  # Fallback: script is at repo root with platform-integrations/ alongside it
  if [ -d "${SCRIPT_DIR}/platform-integrations" ]; then
    SOURCE_DIR="${SCRIPT_DIR}"
    if [ "$KAIZEN_DEBUG" = "1" ]; then
      info "Using local source (same dir): ${SOURCE_DIR}"
    fi
    return
  fi

  # Remote: download tarball
  info "Downloading kaizen source (${KAIZEN_VERSION})..."

  for cmd in curl tar; do
    command -v "$cmd" &>/dev/null || die "'$cmd' is required for remote install but not found."
  done

  TMPDIR_DOWNLOAD="$(mktemp -d)"
  trap 'rm -rf "$TMPDIR_DOWNLOAD"' EXIT

  local url
  if [ "$KAIZEN_VERSION" = "main" ] || [ "$KAIZEN_VERSION" = "latest" ]; then
    url="https://github.com/${KAIZEN_REPO}/archive/refs/heads/main.tar.gz"
  else
    url="https://github.com/${KAIZEN_REPO}/archive/refs/tags/${KAIZEN_VERSION}.tar.gz"
  fi

  if ! curl -fsSL "$url" | tar -xz -C "$TMPDIR_DOWNLOAD" --strip-components=1; then
    die "Failed to download or extract kaizen from: ${url}"
  fi

  if [ ! -d "${TMPDIR_DOWNLOAD}/platform-integrations" ]; then
    die "Downloaded archive does not contain platform-integrations/. Check KAIZEN_REPO and KAIZEN_VERSION."
  fi

  SOURCE_DIR="$TMPDIR_DOWNLOAD"
  success "Downloaded kaizen ${KAIZEN_VERSION}"
}

resolve_source

# ─── Hand off to Python ───────────────────────────────────────────────────────
# Pass SOURCE_DIR as argv[1], then all original CLI args.
# The heredoc uses single-quoted PYEOF so bash does not interpolate inside it.

exec python3 - "$SOURCE_DIR" "$@" <<'PYEOF'
import argparse
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

KAIZEN_DEBUG = os.environ.get("KAIZEN_DEBUG", "0") == "1"
DRY_RUN = False   # set to True by --dry-run flag; checked in all write primitives

BOB_SLUG    = "kaizen-lite"
ROO_SLUG    = "kaizen-lite"
CLAUDE_PLUGIN = "kaizen-lite"


# ── Colour helpers ────────────────────────────────────────────────────────────
IS_TTY = sys.stdout.isatty()
def _c(code, text): return f"\033[{code}m{text}\033[0m" if IS_TTY else text
def info(msg):    print(_c("36", "→") + " " + msg)
def success(msg): print(_c("32", "✓") + " " + msg)
def warn(msg):    print(_c("33", "⚠") + " " + msg)
def error(msg):   print(_c("31", "✗") + " " + msg, file=sys.stderr)
def debug(msg):
    if KAIZEN_DEBUG: print(_c("35", "·") + " " + msg)
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
    tmp = path + ".kaizen.tmp"
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
    tmp = path + ".kaizen.tmp"
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
        bak = path + ".kaizen.bak"
        warn(f"Could not parse {path} — backing up to {bak} and starting fresh.")
        shutil.copy2(path, bak)
        return {}


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
    shutil.copytree(src, dst, dirs_exist_ok=True)
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

def upsert_json_key(path, key_path: list, value):
    """Upsert a nested key into a JSON file. key_path = ['a', 'b', 'c'] → data['a']['b']['c'] = value."""
    data = read_json(path)
    cursor = data
    for key in key_path[:-1]:
        cursor = cursor.setdefault(key, {})
    cursor[key_path[-1]] = value
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
            arr[i] = item
            break
    else:
        arr.append(item)
    atomic_write_json(path, data)


def remove_json_array_item(path, array_key: str, id_key: str, id_val: str):
    """Remove an item from a JSON array by identity key."""
    if not os.path.isfile(str(path)):
        return
    data = read_json(path)
    arr = data.get(array_key, [])
    data[array_key] = [item for item in arr if item.get(id_key) != id_val]
    atomic_write_json(path, data)


# ── YAML helpers ───────────────────────────────────────────────────────────────

def _sentinel_start(slug): return f"# >>>kaizen:{slug}<<<"
def _sentinel_end(slug):   return f"# <<<kaizen:{slug}<<<"


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


def load_roo_mode_from_yaml(source_path):
    """
    Extract the kaizen-lite mode dict from the Roo source .roomodes YAML file
    using regex, returning a dict suitable for JSON insertion.
    """
    with open(str(source_path)) as f:
        text = f.read()

    def extract(field):
        # Match simple single-line values at mode definition level (2-4 spaces, optionally with list dash)
        # First try to match with list dash (for slug field: "  - slug: value")
        m = re.search(rf"^  - {field}:\s+(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"')
        # Then try without dash (for other fields: "    name: value")
        m = re.search(rf"^    {field}:\s+(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"')
        return ""

    def extract_block(field):
        # Match block scalar (|- or |) at mode definition level (4 spaces, no list dash)
        m = re.search(rf"^    {field}:\s*\|-?\s*\n((?:[ \t]+.+\n?)*)", text, re.MULTILINE)
        if m:
            lines = m.group(1).splitlines()
            # Find minimum indent
            min_indent = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
            return "\n".join(l[min_indent:] for l in lines)
        return extract(field)

    def extract_list(field):
        # Match list field at mode definition level (4 spaces, no list dash before field name)
        m = re.search(rf"^    {field}:\s*\n((?:      - .+\n?)*)", text, re.MULTILINE)
        if m:
            return [re.sub(r"^\s+- ", "", l) for l in m.group(1).splitlines() if l.strip()]
        return []

    slug = extract("slug")
    if not slug:
        return None

    return {
        "slug": slug,
        "name": extract("name"),
        "firstMessage": extract("firstMessage"),
        "roleDefinition": extract_block("roleDefinition"),
        "customInstructions": extract_block("customInstructions"),
        "groups": extract_list("groups"),
        "description": extract("description"),
    }


# ── Platform detection ─────────────────────────────────────────────────────────

def detect_platforms(target_dir):
    target = Path(target_dir)
    return {
        "bob": (
            shutil.which("bob") is not None or
            (target / ".bob").is_dir()
        ),
        "roo": (
            shutil.which("roo") is not None or
            shutil.which("roo-code") is not None or
            (target / ".roomodes").is_file() or
            (target / ".roo").is_dir()
        ),
        "claude": (
            shutil.which("claude") is not None or
            (target / ".claude").is_dir()
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
    bob_source_lite = Path(source_dir) / "platform-integrations" / "bob" / "kaizen-lite"
    bob_target = Path(target_dir) / ".bob"

    info(f"Installing Bob ({mode} mode) → {bob_target}")

    # Skills
    copy_tree(bob_source_lite / "skills" / "kaizen-learn",  bob_target / "skills" / "kaizen-learn")
    copy_tree(bob_source_lite / "skills" / "kaizen-recall", bob_target / "skills" / "kaizen-recall")
    success("Copied Bob skills")

    # Commands
    copy_tree(bob_source_lite / "commands", bob_target / "commands")
    success("Copied Bob commands")

    # custom_modes.yaml
    source_modes_yaml = bob_source_lite / "custom_modes.yaml"
    target_modes_yaml = bob_target / "custom_modes.yaml"
    merge_yaml_custom_mode(source_modes_yaml, target_modes_yaml, BOB_SLUG)
    success(f"Merged custom mode '{BOB_SLUG}' into {target_modes_yaml}")

    # Full mode: mcp.json
    if mode == "full":
        mcp_source = Path(source_dir) / "platform-integrations" / "bob" / "kaizen-full" / "mcp.json"
        if not mcp_source.exists():
            error(f"Source MCP config not found: {mcp_source}")
            sys.exit(1)
        mcp_target = bob_target / "mcp.json"
        with open(mcp_source) as f:
            mcp_data = json.load(f)
        kaizen_server = mcp_data["mcpServers"]["kaizen"]
        upsert_json_key(mcp_target, ["mcpServers", "kaizen"], kaizen_server)
        success(f"Upserted MCP server config in {mcp_target}")

    success("Bob installation complete")


def uninstall_bob(target_dir, mode="full"):
    bob_target = Path(target_dir) / ".bob"
    info(f"Uninstalling Bob from {bob_target}")

    remove_dir(bob_target / "skills" / "kaizen-learn")
    remove_dir(bob_target / "skills" / "kaizen-recall")
    remove_file(bob_target / "commands" / "kaizen:learn.md")
    remove_file(bob_target / "commands" / "kaizen:recall.md")
    remove_yaml_custom_mode(bob_target / "custom_modes.yaml", BOB_SLUG)
    remove_json_key(bob_target / "mcp.json", ["mcpServers", "kaizen"])

    success("Bob uninstall complete")


def status_bob(target_dir):
    bob_target = Path(target_dir) / ".bob"
    print(f"  Bob (.bob/):")
    print(f"    skills/kaizen-learn  : {'✓' if (bob_target / 'skills' / 'kaizen-learn').is_dir() else '✗'}")
    print(f"    skills/kaizen-recall : {'✓' if (bob_target / 'skills' / 'kaizen-recall').is_dir() else '✗'}")
    print(f"    commands/            : {'✓' if (bob_target / 'commands' / 'kaizen:learn.md').is_file() else '✗'}")
    print(f"    custom_modes.yaml    : {'✓' if (bob_target / 'custom_modes.yaml').is_file() else '✗'}")

    mcp_path = bob_target / "mcp.json"
    has_mcp = False
    if mcp_path.is_file():
        mcp = read_json(mcp_path)
        has_mcp = "kaizen" in mcp.get("mcpServers", {})
    print(f"    mcp.json (full mode) : {'✓' if has_mcp else '✗'}")


# ── Roo installer ─────────────────────────────────────────────────────────────

def install_roo(source_dir, target_dir):
    roo_source = Path(source_dir) / "platform-integrations" / "roo" / "kaizen-lite"
    roo_skills_source = roo_source / "skills"

    info(f"Installing Roo → {target_dir}")

    # Skill directories (exclude .roomodes from copy)
    copy_tree(roo_skills_source / "kaizen-learn",  Path(target_dir) / ".roo" / "skills" / "kaizen-learn")
    copy_tree(roo_skills_source / "kaizen-recall", Path(target_dir) / ".roo" / "skills" / "kaizen-recall")
    success("Copied Roo skills")

    # .roomodes — detect target format
    roomodes_source = roo_skills_source / ".roomodes"   # YAML format in source
    roomodes_target = Path(target_dir) / ".roomodes"

    if not roomodes_target.is_file():
        # Target doesn't exist — create as YAML (roo-code preferred format)
        merge_yaml_custom_mode(roomodes_source, roomodes_target, ROO_SLUG)
        success(f"Created {roomodes_target} with mode '{ROO_SLUG}' (YAML)")
    elif is_json_file(roomodes_target):
        # Target exists and is JSON — load mode from YAML source and upsert into JSON
        mode_dict = load_roo_mode_from_yaml(roomodes_source)
        if mode_dict is None:
            warn(f"Could not extract mode '{ROO_SLUG}' from {roomodes_source}")
        else:
            upsert_json_array_item(roomodes_target, "customModes", mode_dict, "slug")
            success(f"Upserted mode '{ROO_SLUG}' into {roomodes_target} (JSON)")
    else:
        # Target exists and is YAML — use sentinel merge
        merge_yaml_custom_mode(roomodes_source, roomodes_target, ROO_SLUG)
        success(f"Merged mode '{ROO_SLUG}' into {roomodes_target} (YAML)")

    success("Roo installation complete")


def uninstall_roo(target_dir):
    info(f"Uninstalling Roo from {target_dir}")
    remove_dir(Path(target_dir) / ".roo" / "skills" / "kaizen-learn")
    remove_dir(Path(target_dir) / ".roo" / "skills" / "kaizen-recall")

    roomodes_target = Path(target_dir) / ".roomodes"
    if roomodes_target.is_file():
        if is_json_file(roomodes_target):
            remove_json_array_item(roomodes_target, "customModes", "slug", ROO_SLUG)
        else:
            remove_yaml_custom_mode(roomodes_target, ROO_SLUG)

    success("Roo uninstall complete")


def status_roo(target_dir):
    roo_skills = Path(target_dir) / ".roo" / "skills"
    roomodes = Path(target_dir) / ".roomodes"
    print(f"  Roo (.roo/ + .roomodes):")
    print(f"    .roo/skills/kaizen-learn  : {'✓' if (roo_skills / 'kaizen-learn').is_dir() else '✗'}")
    print(f"    .roo/skills/kaizen-recall : {'✓' if (roo_skills / 'kaizen-recall').is_dir() else '✗'}")

    mode_present = False
    if roomodes.is_file():
        if is_json_file(roomodes):
            data = read_json(roomodes)
            mode_present = any(m.get("slug") == ROO_SLUG for m in data.get("customModes", []))
        else:
            with open(roomodes) as f:
                mode_present = f"slug: {ROO_SLUG}" in f.read()
    print(f"    .roomodes (kaizen-lite)   : {'✓' if mode_present else '✗'}")


# ── Claude installer ──────────────────────────────────────────────────────────

def install_claude(source_dir, target_dir):
    plugin_source = Path(source_dir) / "platform-integrations" / "claude" / "plugins" / "kaizen-lite"
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
        print(f"    kaizen-lite plugin  : {'✓' if installed else '✗ (not installed)'}")
    except Exception:
        print(f"    kaizen-lite plugin  : ? (could not query)")


# ── Dispatch ──────────────────────────────────────────────────────────────────

def cmd_install(args):
    global DRY_RUN
    DRY_RUN = args.dry_run
    target_dir = os.path.abspath(args.dir)

    # Resolve platforms
    if args.platform == "all":
        platforms = ["bob", "roo", "claude"]
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
            elif platform == "roo":
                install_roo(SOURCE_DIR, target_dir)
            elif platform == "claude":
                install_claude(SOURCE_DIR, target_dir)
        except Exception as e:
            error(f"Failed to install {platform}: {e}")
            if KAIZEN_DEBUG:
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
        platforms = ["bob", "roo", "claude"]
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
            elif platform == "roo":
                uninstall_roo(target_dir)
            elif platform == "claude":
                uninstall_claude(target_dir)
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
    print(f"Kaizen installation status in: {target_dir}")
    print()
    status_bob(target_dir)
    print()
    status_roo(target_dir)
    print()
    status_claude(target_dir)
    print()


# ── argparse ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="install.sh",
        description="Install Kaizen integrations for Bob, Roo, and Claude Code.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # install
    p_install = sub.add_parser("install", help="Install Kaizen into the current project")
    p_install.add_argument(
        "--platform", choices=["bob", "roo", "claude", "all"], default=None,
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
    p_uninstall = sub.add_parser("uninstall", help="Remove Kaizen from the current project")
    p_uninstall.add_argument(
        "--platform", choices=["bob", "roo", "claude", "all"], default=None,
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
