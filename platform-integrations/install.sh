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
# Pinned version:
#   curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/v1.2.0/platform-integrations/install.sh | bash

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────
EVOLVE_REPO="${EVOLVE_REPO:-AgentToolkit/altk-evolve}"
export EVOLVE_REPO
EVOLVE_DEBUG="${EVOLVE_DEBUG:-0}"

# SCRIPT_VERSION refers to a branch or a version tag. This value is substituted
# during the release process, so that a script always knows it's own version,
# and downloads the correct artifact bundle.
# Callers can manually override: EVOLVE_VERSION=v1.0.6 bash install.sh ...
SCRIPT_VERSION="main"
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

  # No local source found; Python will download on demand if needed.
  SOURCE_DIR=""
}

resolve_source

# ─── Hand off to Python ───────────────────────────────────────────────────────
# Pass SOURCE_DIR as argv[1], then all original CLI args.
# The heredoc uses single-quoted PYEOF so bash does not interpolate inside it.

exec python3 -u - "$SOURCE_DIR" "$@" <<'PYEOF'
import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────
SOURCE_DIR = sys.argv[1]
CLI_ARGS   = sys.argv[2:]

EVOLVE_DEBUG   = os.environ.get("EVOLVE_DEBUG", "0") == "1"
EVOLVE_REPO    = os.environ.get("EVOLVE_REPO", "AgentToolkit/altk-evolve")
EVOLVE_VERSION = os.environ.get("EVOLVE_VERSION", "main")
DRY_RUN = False

BOB_SLUG      = "evolve-lite"
CLAUDE_PLUGIN = "evolve-lite"
CODEX_PLUGIN  = "evolve-lite"


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


# ── Source resolution ─────────────────────────────────────────────────────────
_tmpdir_download = None

def _ensure_source_dir():
    """Download the evolve source tarball if SOURCE_DIR was not resolved locally."""
    global SOURCE_DIR, _tmpdir_download
    if SOURCE_DIR:
        return
    if DRY_RUN:
        dryrun("would download evolve source (skipped in dry-run)")
        return

    import atexit, tempfile
    info(f"Downloading evolve source ({EVOLVE_VERSION})...")

    for cmd in ("curl", "tar"):
        if not shutil.which(cmd):
            raise RuntimeError(f"'{cmd}' is required for remote install but not found.")

    _tmpdir_download = tempfile.mkdtemp()
    atexit.register(lambda: shutil.rmtree(_tmpdir_download, ignore_errors=True))

    if EVOLVE_VERSION in ("main", "latest"):
        url = f"https://github.com/{EVOLVE_REPO}/archive/refs/heads/main.tar.gz"
    else:
        url = f"https://github.com/{EVOLVE_REPO}/archive/refs/tags/{EVOLVE_VERSION}.tar.gz"

    curl = subprocess.Popen(["curl", "-fsSL", url], stdout=subprocess.PIPE)
    tar  = subprocess.run(["tar", "-xz", "-C", _tmpdir_download, "--strip-components=1"], stdin=curl.stdout)
    curl.wait()
    if curl.returncode != 0 or tar.returncode != 0:
        raise RuntimeError(f"Failed to download or extract evolve from: {url}")
    if not os.path.isdir(os.path.join(_tmpdir_download, "platform-integrations")):
        raise RuntimeError("Downloaded archive does not contain platform-integrations/. Check EVOLVE_REPO and EVOLVE_VERSION.")

    SOURCE_DIR = _tmpdir_download
    success(f"Downloaded evolve {EVOLVE_VERSION}")


# ── Read-only helpers (no side effects) ───────────────────────────────────────

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


def merge_json_value(existing, desired):
    """Recursively merge JSON-like values, preserving unknown keys from existing objects."""
    if isinstance(existing, dict) and isinstance(desired, dict):
        merged = copy.deepcopy(existing)
        for key, desired_value in desired.items():
            merged[key] = merge_json_value(merged.get(key), desired_value)
        return merged
    return copy.deepcopy(desired)


def _sentinel_start(slug): return f"# >>>evolve:{slug}<<<"
def _sentinel_end(slug):   return f"# <<<evolve:{slug}<<<"

def _safe_copy2(src, dst):
    """Like shutil.copy2 but skips when src and dst are the same file (hardlink/APFS clone)."""
    if os.path.exists(dst) and os.path.samefile(src, dst):
        debug(f"Skipping (same file): {src} → {dst}")
        return
    try:
        shutil.copy2(src, dst)
    except shutil.SameFileError:
        debug(f"Skipping (same file): {src} → {dst}")


# ── File operations ───────────────────────────────────────────────────────────

class FileOps:
    """
    All write operations go through this class. Swap in DryRunFileOps to get
    a no-op run that logs what would happen instead.
    """

    is_dry_run = False

    # ── Primitives ────────────────────────────────────────────────────────────

    def atomic_write_json(self, path, data):
        path = str(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".evolve.tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        debug(f"Wrote JSON: {path}")

    def atomic_write_text(self, path, text):
        path = str(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".evolve.tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)
        debug(f"Wrote text: {path}")

    def copy_tree(self, src, dst):
        src, dst = str(src), str(dst)
        if not os.path.isdir(src):
            raise FileNotFoundError(f"Source directory not found: {src}")
        os.makedirs(dst, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=_safe_copy2)
        debug(f"Copied {src} → {dst}")

    def remove_dir(self, path):
        path = str(path)
        if os.path.isdir(path):
            shutil.rmtree(path)
            debug(f"Removed dir: {path}")
            return True
        return False

    def remove_file(self, path):
        path = str(path)
        if os.path.isfile(path):
            os.remove(path)
            debug(f"Removed file: {path}")
            return True
        return False

    def run_subprocess(self, cmd_list):
        return subprocess.run(cmd_list)

    # ── JSON helpers ──────────────────────────────────────────────────────────

    def upsert_json_key(self, path, key_path, value):
        """Upsert a nested key into a JSON file. key_path = ['a', 'b'] → data['a']['b'] = value."""
        data = read_json(path)
        cursor = data
        for key in key_path[:-1]:
            if not isinstance(cursor.get(key), dict):
                cursor[key] = {}
            cursor = cursor[key]
        cursor[key_path[-1]] = merge_json_value(cursor.get(key_path[-1]), value)
        self.atomic_write_json(path, data)

    def remove_json_key(self, path, key_path):
        if not os.path.isfile(str(path)):
            return
        data = read_json(path)
        cursor = data
        for key in key_path[:-1]:
            if key not in cursor:
                return
            cursor = cursor[key]
        cursor.pop(key_path[-1], None)
        self.atomic_write_json(path, data)

    def upsert_json_array_item(self, path, array_key, item, id_key):
        """Upsert an item into a JSON array by identity key."""
        data = read_json(path)
        arr = data.setdefault(array_key, [])
        for i, existing in enumerate(arr):
            if existing.get(id_key) == item.get(id_key):
                arr[i] = merge_json_value(existing, item)
                break
        else:
            arr.append(copy.deepcopy(item))
        self.atomic_write_json(path, data)

    def remove_json_array_item(self, path, array_key, id_key, id_val):
        if not os.path.isfile(str(path)):
            return
        data = read_json(path)
        data[array_key] = [item for item in data.get(array_key, []) if item.get(id_key) != id_val]
        self.atomic_write_json(path, data)

    # ── YAML helpers ──────────────────────────────────────────────────────────

    def merge_yaml_custom_mode(self, source_yaml_path, target_yaml_path, slug):
        """Merge a custom mode entry into a YAML custom_modes file using sentinel blocks."""
        source_yaml_path = str(source_yaml_path)
        target_yaml_path = str(target_yaml_path)

        with open(source_yaml_path) as f:
            source_text = f.read()

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

        if not existing.strip() or "customModes:" not in existing:
            existing = "customModes:\n"

        if start in existing:
            pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
            new_content = pattern.sub(block.strip(), existing)
        else:
            new_content = existing.rstrip() + block

        self.atomic_write_text(target_yaml_path, new_content)
        debug(f"YAML merge (sentinel): {target_yaml_path}")

    def remove_yaml_custom_mode(self, target_yaml_path, slug):
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
        self.atomic_write_text(target_yaml_path, pattern.sub("", text))


class DryRunFileOps(FileOps):
    """No-op variant: logs what would happen instead of writing anything."""

    is_dry_run = True

    def atomic_write_json(self, path, data):
        dryrun(f"write JSON → {path}")
        debug(json.dumps(data, indent=2))

    def atomic_write_text(self, path, text):
        dryrun(f"write text → {path}")

    def copy_tree(self, src, dst):
        src, dst = str(src), str(dst)
        if os.path.isdir(src):
            files = [os.path.relpath(os.path.join(r, f), src)
                     for r, _, fs in os.walk(src) for f in fs]
            dryrun(f"copy dir → {dst}/ ({len(files)} file(s): {', '.join(files[:5])}{'…' if len(files) > 5 else ''})")
        else:
            dryrun(f"copy dir → {dst}/ (source not found: {src})")

    def remove_dir(self, path):
        dryrun(f"remove dir  → {path}")
        return True

    def remove_file(self, path):
        dryrun(f"remove file → {path}")
        return True

    def run_subprocess(self, cmd_list):
        dryrun(f"run: {' '.join(cmd_list)}")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def merge_yaml_custom_mode(self, source_yaml_path, target_yaml_path, slug):
        dryrun(f"merge YAML custom mode '{slug}' → {target_yaml_path}")


# ── Platform detection ────────────────────────────────────────────────────────

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


# ── Bob ───────────────────────────────────────────────────────────────────────

class BobInstaller:
    def __init__(self, ops: FileOps):
        self.ops = ops

    def install(self, target_dir, mode="lite"):
        _ensure_source_dir()
        source_dir = SOURCE_DIR
        bob_source_lite = Path(source_dir) / "platform-integrations" / "bob" / "evolve-lite"
        bob_target = Path(target_dir) / ".bob"

        info(f"Installing Bob ({mode} mode) → {bob_target}")

        if mode == "lite":
            shared_lib = Path(source_dir) / "platform-integrations" / "claude" / "plugins" / "evolve-lite" / "lib"
            if not self.ops.is_dry_run and not shared_lib.is_dir():
                raise RuntimeError(f"Shared lib not found: {shared_lib} — is the Claude plugin present in the source tree?")
            self.ops.copy_tree(shared_lib, bob_target / "evolve-lib")
            success("Copied Bob lib")

            skills_src = bob_source_lite / "skills"
            if not self.ops.is_dry_run and not skills_src.is_dir():
                raise RuntimeError(f"Skills source not found: {skills_src}")
            if skills_src.is_dir():
                for skill_dir in sorted(skills_src.iterdir()):
                    if skill_dir.is_dir():
                        self.ops.copy_tree(skill_dir, bob_target / "skills" / skill_dir.name)
            else:
                self.ops.copy_tree(skills_src, bob_target / "skills")
            success("Copied Bob skills")

            self.ops.copy_tree(bob_source_lite / "commands", bob_target / "commands")
            success("Copied Bob commands")

            self.ops.merge_yaml_custom_mode(
                bob_source_lite / "custom_modes.yaml",
                bob_target / "custom_modes.yaml",
                BOB_SLUG,
            )
            success(f"Merged custom mode '{BOB_SLUG}' into {bob_target / 'custom_modes.yaml'}")

        elif mode == "full":
            bob_source_full = Path(source_dir) / "platform-integrations" / "bob" / "evolve-full"
            mcp_source = bob_source_full / "mcp.json"
            if not self.ops.is_dry_run and not mcp_source.exists():
                raise RuntimeError(f"Source MCP config not found: {mcp_source}")
            if not self.ops.is_dry_run:
                mcp_data = read_json(mcp_source)
                self.ops.upsert_json_key(bob_target / "mcp.json", ["mcpServers", "evolve"], mcp_data["mcpServers"]["evolve"])
            else:
                self.ops.upsert_json_key(bob_target / "mcp.json", ["mcpServers", "evolve"], {})
            success(f"Upserted MCP server config in {bob_target / 'mcp.json'}")

            self.ops.merge_yaml_custom_mode(
                bob_source_full / "custom_modes.yaml",
                bob_target / "custom_modes.yaml",
                "Evolve",
            )
            success(f"Merged custom mode 'Evolve' into {bob_target / 'custom_modes.yaml'}")

        success("Bob installation complete")

    def uninstall(self, target_dir):
        bob_target = Path(target_dir) / ".bob"
        info(f"Uninstalling Bob from {bob_target}")

        self.ops.remove_dir(bob_target / "evolve-lib")
        skills_dir = bob_target / "skills"
        if skills_dir.is_dir():
            for skill_dir in sorted(skills_dir.glob("evolve-lite:*")):
                self.ops.remove_dir(skill_dir)
        commands_dir = bob_target / "commands"
        if commands_dir.is_dir():
            for cmd_file in sorted(commands_dir.glob("evolve-lite:*.md")):
                self.ops.remove_file(cmd_file)
        self.ops.remove_yaml_custom_mode(bob_target / "custom_modes.yaml", BOB_SLUG)
        self.ops.remove_yaml_custom_mode(bob_target / "custom_modes.yaml", "Evolve")
        self.ops.remove_json_key(bob_target / "mcp.json", ["mcpServers", "evolve"])

        success("Bob uninstall complete")

    def status(self, target_dir):
        bob_target = Path(target_dir) / ".bob"
        print(f"  Bob (.bob/):")
        print(f"    evolve-lib/entity_io      : {'✓' if (bob_target / 'evolve-lib' / 'entity_io.py').is_file() else '✗'}")
        skills_dir = bob_target / "skills"
        installed_skills = sorted(skills_dir.glob("evolve-lite:*")) if skills_dir.is_dir() else []
        if installed_skills:
            for s in installed_skills:
                print(f"    skills/{s.name} : ✓")
        else:
            print(f"    skills/evolve-lite:*      : ✗")
        commands_dir = bob_target / "commands"
        installed_cmds = sorted(commands_dir.glob("evolve-lite:*.md")) if commands_dir.is_dir() else []
        print(f"    commands/ ({len(installed_cmds)} evolve commands) : {'✓' if installed_cmds else '✗'}")
        print(f"    custom_modes.yaml         : {'✓' if (bob_target / 'custom_modes.yaml').is_file() else '✗'}")
        has_mcp = "evolve" in read_json(bob_target / "mcp.json").get("mcpServers", {}) if (bob_target / "mcp.json").is_file() else False
        print(f"    mcp.json (full mode)      : {'✓' if has_mcp else '✗'}")


# ── Claude ────────────────────────────────────────────────────────────────────

class ClaudeInstaller:
    def __init__(self, ops: FileOps):
        self.ops = ops

    def install(self, target_dir):
        info("Installing Claude plugin via marketplace")

        marketplace_dir = Path(SOURCE_DIR).resolve() if SOURCE_DIR else None
        has_local_marketplace = marketplace_dir is not None and (marketplace_dir / ".claude-plugin" / "marketplace.json").is_file()
        marketplace_source = str(marketplace_dir) if has_local_marketplace else EVOLVE_REPO
        if has_local_marketplace:
            info(f"📁 Marketplace source: {_c('1', marketplace_source)} (local)")
        else:
            info(f"🌐 Marketplace source: {_c('1', marketplace_source)} (GitHub)")

        claude = shutil.which("claude")
        if not claude:
            warn("Claude CLI not found. Install it from https://claude.ai/download, then re-run this script.")
            return

        result = self.ops.run_subprocess([claude, "plugin", "marketplace", "add", marketplace_source])
        if result.returncode != 0:
            warn(f"claude plugin marketplace add exited with code {result.returncode}")
            warn("To install manually, run:")
            print()
            print(f"    claude plugin marketplace add {marketplace_source}")
            print(f"    claude plugin install evolve-lite@evolve-marketplace")
            print()
            return

        result = self.ops.run_subprocess([claude, "plugin", "install", "evolve-lite@evolve-marketplace"])
        if result.returncode == 0:
            if self.ops.is_dry_run:
                dryrun("Claude plugin would be installed via CLI")
            else:
                success("Claude plugin installed via CLI")
        else:
            warn(f"claude plugin install exited with code {result.returncode}")
            warn("To install manually, run:")
            print()
            print(f"    claude plugin marketplace add {marketplace_source}")
            print(f"    claude plugin install evolve-lite@evolve-marketplace")
            print()

    def uninstall(self, target_dir):
        info("Uninstalling Claude plugin")
        claude = shutil.which("claude")
        if not claude:
            warn("Could not uninstall Claude plugin automatically.")
            warn(f"Run manually: claude plugin uninstall {CLAUDE_PLUGIN}")
            return

        result = self.ops.run_subprocess([claude, "plugin", "uninstall", CLAUDE_PLUGIN])
        if result.returncode == 0:
            success("Claude plugin uninstalled via CLI")
        else:
            warn(f"claude plugin uninstall exited with code {result.returncode}")
            warn(f"Run manually: claude plugin uninstall {CLAUDE_PLUGIN}")

    def status(self, target_dir):
        print(f"  Claude:")
        claude = shutil.which("claude")
        if not claude:
            print(f"    claude CLI          : ✗ (not found on PATH)")
            return
        print(f"    claude CLI          : ✓")
        try:
            result = subprocess.run([claude, "plugin", "list"], capture_output=True, text=True)
            installed = CLAUDE_PLUGIN in result.stdout
            print(f"    evolve-lite plugin  : {'✓' if installed else '✗ (not installed)'}")
        except Exception:
            print(f"    evolve-lite plugin  : ? (could not query)")


# ── Codex ─────────────────────────────────────────────────────────────────────

class CodexInstaller:
    def __init__(self, ops: FileOps):
        self.ops = ops

    # ── Codex hook/marketplace schema helpers ─────────────────────────────────

    @staticmethod
    def _recall_hook_command():
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

    @staticmethod
    def _is_recall_command(command):
        return isinstance(command, str) and "plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py" in command

    @staticmethod
    def _recall_hook():
        return {
            "type": "command",
            "command": CodexInstaller._recall_hook_command(),
            "statusMessage": "Loading Evolve guidance",
        }

    @staticmethod
    def _recall_hook_group():
        return {"matcher": "", "hooks": [CodexInstaller._recall_hook()]}

    @staticmethod
    def _sync_hook_command():
        return (
            "sh -lc '"
            'd=\"$PWD\"; '
            "while :; do "
            'candidate=\"$d/plugins/evolve-lite/skills/sync/scripts/sync.py\"; '
            'if [ -f \"$candidate\" ]; then EVOLVE_DIR=\"$d/.evolve\" exec python3 \"$candidate\" --quiet --session-start; fi; '
            '[ \"$d\" = \"/\" ] && break; '
            'd=\"$(dirname \"$d\")\"; '
            "done; "
            "exit 1'"
        )

    @staticmethod
    def _is_sync_command(command):
        return isinstance(command, str) and "plugins/evolve-lite/skills/sync/scripts/sync.py" in command

    @staticmethod
    def _sync_hook():
        return {
            "type": "command",
            "command": CodexInstaller._sync_hook_command(),
            "statusMessage": "Syncing Evolve subscriptions",
        }

    @staticmethod
    def _sync_hook_group():
        return {"matcher": "startup|resume", "hooks": [CodexInstaller._sync_hook()]}

    @staticmethod
    def _iter_group_hooks(group):
        hooks = group.get("hooks", [])
        if isinstance(hooks, list): return hooks
        if isinstance(hooks, dict): return list(hooks.values())
        return []

    @staticmethod
    def _group_has_recall(group):
        return any(
            isinstance(h, dict) and CodexInstaller._is_recall_command(h.get("command"))
            for h in CodexInstaller._iter_group_hooks(group)
        )

    @staticmethod
    def _group_has_sync(group):
        return any(
            isinstance(h, dict) and CodexInstaller._is_sync_command(h.get("command"))
            for h in CodexInstaller._iter_group_hooks(group)
        )

    @staticmethod
    def _upsert_recall_into_group(group):
        updated = copy.deepcopy(group)
        recall = CodexInstaller._recall_hook()
        hooks = updated.get("hooks")
        if isinstance(hooks, list):
            for i, h in enumerate(hooks):
                if isinstance(h, dict) and CodexInstaller._is_recall_command(h.get("command")):
                    hooks[i] = merge_json_value(h, recall)
                    break
            else:
                hooks.append(copy.deepcopy(recall))
        elif isinstance(hooks, dict):
            for key, h in hooks.items():
                if isinstance(h, dict) and CodexInstaller._is_recall_command(h.get("command")):
                    hooks[key] = merge_json_value(h, recall)
                    break
            else:
                hooks["evolve-lite"] = copy.deepcopy(recall)
        else:
            updated["hooks"] = [copy.deepcopy(recall)]
        return updated

    @staticmethod
    def _upsert_sync_into_group(group):
        updated = copy.deepcopy(group)
        sync = CodexInstaller._sync_hook()
        hooks = updated.get("hooks")
        if isinstance(hooks, list):
            for i, h in enumerate(hooks):
                if isinstance(h, dict) and CodexInstaller._is_sync_command(h.get("command")):
                    hooks[i] = merge_json_value(h, sync)
                    break
            else:
                hooks.append(copy.deepcopy(sync))
        elif isinstance(hooks, dict):
            for key, h in hooks.items():
                if isinstance(h, dict) and CodexInstaller._is_sync_command(h.get("command")):
                    hooks[key] = merge_json_value(h, sync)
                    break
            else:
                hooks["evolve-lite"] = copy.deepcopy(sync)
        else:
            updated["hooks"] = [copy.deepcopy(sync)]
        return updated

    @staticmethod
    def _remove_recall_from_group(group):
        updated = copy.deepcopy(group)
        hooks = updated.get("hooks")
        if isinstance(hooks, list):
            updated["hooks"] = [
                h for h in hooks
                if not (isinstance(h, dict) and CodexInstaller._is_recall_command(h.get("command")))
            ]
        elif isinstance(hooks, dict):
            updated["hooks"] = {
                k: h for k, h in hooks.items()
                if not (isinstance(h, dict) and CodexInstaller._is_recall_command(h.get("command")))
            }
        return updated

    @staticmethod
    def _remove_sync_from_group(group):
        updated = copy.deepcopy(group)
        hooks = updated.get("hooks")
        if isinstance(hooks, list):
            updated["hooks"] = [
                h for h in hooks
                if not (isinstance(h, dict) and CodexInstaller._is_sync_command(h.get("command")))
            ]
        elif isinstance(hooks, dict):
            updated["hooks"] = {
                k: h for k, h in hooks.items()
                if not (isinstance(h, dict) and CodexInstaller._is_sync_command(h.get("command")))
            }
        return updated

    def _upsert_marketplace_entry(self, path, item):
        data = read_json(path)
        if not data:
            data = {"name": "evolve-local", "interface": {"displayName": "Evolve Local Plugins"}, "plugins": []}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a JSON object.")
        data.setdefault("name", "evolve-local")
        data.setdefault("interface", {}).setdefault("displayName", "Evolve Local Plugins")
        plugins = data.setdefault("plugins", [])
        if not isinstance(plugins, list):
            raise ValueError(f"{path} field 'plugins' must be an array.")
        for i, existing in enumerate(plugins):
            if isinstance(existing, dict) and existing.get("name") == item.get("name"):
                plugins[i] = merge_json_value(existing, item)
                break
        else:
            plugins.append(copy.deepcopy(item))
        self.ops.atomic_write_json(path, data)

    def _upsert_user_prompt_hook(self, path, group):
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
        for i, existing in enumerate(groups):
            if isinstance(existing, dict) and self._group_has_recall(existing):
                groups[i] = self._upsert_recall_into_group(existing)
                break
        else:
            groups.append(copy.deepcopy(group))
        self.ops.atomic_write_json(path, data)

    def _remove_user_prompt_hook(self, path):
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
            self._remove_recall_from_group(g) if isinstance(g, dict) and self._group_has_recall(g) else g
            for g in groups
        ]
        # Prune empty groups (groups with no hooks left)
        hooks["UserPromptSubmit"] = [
            group for group in hooks["UserPromptSubmit"]
            if not isinstance(group, dict) or self._iter_group_hooks(group)
        ]
        if not hooks["UserPromptSubmit"]:
            hooks.pop("UserPromptSubmit", None)
        self.ops.atomic_write_json(path, data)

    def _upsert_session_start_hook(self, path, group):
        data = read_json(path)
        if not data:
            data = {"hooks": {}}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a JSON object.")
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            hooks = {}
            data["hooks"] = hooks
        groups = hooks.setdefault("SessionStart", [])
        if not isinstance(groups, list):
            groups = []
            hooks["SessionStart"] = groups
        for i, existing in enumerate(groups):
            if isinstance(existing, dict) and self._group_has_sync(existing):
                groups[i] = self._upsert_sync_into_group(existing)
                break
        else:
            groups.append(copy.deepcopy(group))
        self.ops.atomic_write_json(path, data)

    def _remove_session_start_hook(self, path):
        if not os.path.isfile(str(path)):
            return
        data = read_json(path)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return
        groups = hooks.get("SessionStart", [])
        if not isinstance(groups, list):
            return
        hooks["SessionStart"] = [
            self._remove_sync_from_group(g) if isinstance(g, dict) and self._group_has_sync(g) else g
            for g in groups
        ]
        hooks["SessionStart"] = [
            group for group in hooks["SessionStart"]
            if not isinstance(group, dict) or len(self._iter_group_hooks(group)) > 0
        ]
        if not hooks["SessionStart"]:
            hooks.pop("SessionStart", None)
        self.ops.atomic_write_json(path, data)

    # ── Public interface ──────────────────────────────────────────────────────

    def install(self, target_dir):
        _ensure_source_dir()
        source_dir = SOURCE_DIR
        plugin_source = Path(source_dir) / "platform-integrations" / "codex" / "plugins" / CODEX_PLUGIN
        plugin_target = Path(target_dir) / "plugins" / CODEX_PLUGIN
        info(f"Installing Codex → {plugin_target}")

        self.ops.copy_tree(plugin_source, plugin_target)
        success("Copied Codex plugin")

        shared_lib = Path(source_dir) / "platform-integrations" / "claude" / "plugins" / "evolve-lite" / "lib"
        if not self.ops.is_dry_run and not shared_lib.is_dir():
            raise RuntimeError(f"Shared lib not found: {shared_lib} — is the Claude plugin present in the source tree?")
        self.ops.copy_tree(shared_lib, plugin_target / "lib")
        success("Copied Codex lib")

        marketplace_target = Path(target_dir) / ".agents" / "plugins" / "marketplace.json"
        self._upsert_marketplace_entry(
            marketplace_target,
            {
                "name": CODEX_PLUGIN,
                "source": {"source": "local", "path": f"./plugins/{CODEX_PLUGIN}"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            },
        )
        success(f"Upserted Codex marketplace entry in {marketplace_target}")

        hooks_target = Path(target_dir) / ".codex" / "hooks.json"
        self._upsert_user_prompt_hook(hooks_target, self._recall_hook_group())
        self._upsert_session_start_hook(hooks_target, self._sync_hook_group())
        success(f"Upserted Codex UserPromptSubmit hook in {hooks_target}")
        success(f"Upserted Codex SessionStart hook in {hooks_target}")
        warn("Automatic Codex recall requires hooks to be enabled in ~/.codex/config.toml:")
        print("      [features]")
        print("      codex_hooks = true")
        info("If you do not want to enable Codex hooks, invoke the installed evolve-lite:recall skill manually.")

        success("Codex installation complete")

    def uninstall(self, target_dir):
        info(f"Uninstalling Codex from {target_dir}")

        self.ops.remove_dir(Path(target_dir) / "plugins" / CODEX_PLUGIN)
        self.ops.remove_json_array_item(
            Path(target_dir) / ".agents" / "plugins" / "marketplace.json",
            "plugins", "name", CODEX_PLUGIN,
        )
        self._remove_user_prompt_hook(Path(target_dir) / ".codex" / "hooks.json")
        self._remove_session_start_hook(Path(target_dir) / ".codex" / "hooks.json")

        success("Codex uninstall complete")

    def status(self, target_dir):
        plugin_dir = Path(target_dir) / "plugins" / CODEX_PLUGIN
        print("  Codex:")
        print(f"    plugins/evolve-lite       : {'✓' if plugin_dir.is_dir() else '✗'}")
        print(f"    lib/entity_io.py          : {'✓' if (plugin_dir / 'lib' / 'entity_io.py').is_file() else '✗'}")
        print(f"    skills/learn              : {'✓' if (plugin_dir / 'skills' / 'learn').is_dir() else '✗'}")
        print(f"    skills/recall             : {'✓' if (plugin_dir / 'skills' / 'recall').is_dir() else '✗'}")

        marketplace_path = Path(target_dir) / ".agents" / "plugins" / "marketplace.json"
        marketplace_present = (
            any(p.get("name") == CODEX_PLUGIN for p in read_json(marketplace_path).get("plugins", []))
            if marketplace_path.is_file() else False
        )
        print(f"    marketplace.json entry    : {'✓' if marketplace_present else '✗'}")

        hooks_path = Path(target_dir) / ".codex" / "hooks.json"
        hook_present = (
            any(isinstance(g, dict) and self._group_has_recall(g)
                for g in read_json(hooks_path).get("hooks", {}).get("UserPromptSubmit", []))
            if hooks_path.is_file() else False
        )
        session_hook_present = (
            any(isinstance(g, dict) and self._group_has_sync(g)
                for g in read_json(hooks_path).get("hooks", {}).get("SessionStart", []))
            if hooks_path.is_file() else False
        )
        print(f"    .codex/hooks.json entry   : {'✓' if hook_present else '✗'}")
        print(f"    SessionStart sync hook    : {'✓' if session_hook_present else '✗'}")


# ── Dispatch ──────────────────────────────────────────────────────────────────

PLATFORM_CLASSES = {
    "bob":    BobInstaller,
    "claude": ClaudeInstaller,
    "codex":  CodexInstaller,
}


def cmd_install(args):
    target_dir = os.path.abspath(args.dir)
    ops = DryRunFileOps() if DRY_RUN else FileOps()

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
    for i, platform in enumerate(platforms):
        if i > 0:
            print()
        try:
            installer = PLATFORM_CLASSES[platform](ops)
            if platform == "bob":
                installer.install(target_dir, mode=args.mode)
            else:
                installer.install(target_dir)
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
        success("Dry run complete — no changes were made." if DRY_RUN else "All installations complete.")


def cmd_uninstall(args):
    target_dir = os.path.abspath(args.dir)
    ops = DryRunFileOps() if DRY_RUN else FileOps()

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
    for i, platform in enumerate(platforms):
        if i > 0:
            print()
        try:
            PLATFORM_CLASSES[platform](ops).uninstall(target_dir)
        except Exception as e:
            error(f"Failed to uninstall {platform}: {e}")
            errors.append(platform)

    print()
    if errors:
        warn(f"Uninstall completed with errors on: {', '.join(errors)}")
        sys.exit(1)
    else:
        success("Dry run complete — no changes were made." if DRY_RUN else "Uninstall complete.")


def cmd_status(args):
    target_dir = os.path.abspath(args.dir)
    ops = FileOps()
    print()
    print(f"Evolve installation status in: {target_dir}")
    print()
    BobInstaller(ops).status(target_dir)
    print()
    ClaudeInstaller(ops).status(target_dir)
    print()
    CodexInstaller(ops).status(target_dir)
    print()


# ── argparse ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="install.sh",
        description="Install Evolve integrations for Bob, Claude Code, and Codex.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="Install Evolve into the current project")
    p_install.add_argument(
        "--platform", choices=["bob", "claude", "codex", "all"], default=None,
        help="Platform to install (default: auto-detect and prompt)",
    )
    p_install.add_argument(
        "--mode", choices=["lite", "full"], default="lite",
        help="Installation mode for Bob (default: lite)",
    )
    p_install.add_argument("--dir", default=os.getcwd(), help="Target project directory (default: cwd)")
    p_install.add_argument("--dry-run", action="store_true", default=False,
                           help="Show what would be done without making any changes")

    p_uninstall = sub.add_parser("uninstall", help="Remove Evolve from the current project")
    p_uninstall.add_argument(
        "--platform", choices=["bob", "claude", "codex", "all"], default=None,
        help="Platform to uninstall (default: prompt)",
    )
    p_uninstall.add_argument("--dir", default=os.getcwd(), help="Target project directory (default: cwd)")
    p_uninstall.add_argument("--dry-run", action="store_true", default=False,
                             help="Show what would be done without making any changes")

    p_status = sub.add_parser("status", help="Show what is currently installed")
    p_status.add_argument("--dir", default=os.getcwd(), help="Target project directory (default: cwd)")

    args = parser.parse_args(CLI_ARGS)

    global DRY_RUN
    DRY_RUN = getattr(args, "dry_run", False)

    if args.command == "install":
        cmd_install(args)
    elif args.command == "uninstall":
        cmd_uninstall(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()

PYEOF
