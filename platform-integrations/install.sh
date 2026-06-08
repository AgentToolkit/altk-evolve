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

BOB_SLUG          = "evolve-lite"
BOB_RULES_FILE    = "00-evolve-lite.md"
AUDIT_SCRIPT      = "audit_recall.py"
CLAUDE_PLUGIN     = "evolve-lite"
CLAW_CODE_PLUGIN  = "evolve-lite"
CODEX_PLUGIN      = "evolve-lite"

# Marker used to manage a single greppable instruction line that an installer
# injects into an agent's always-on instruction file (e.g. ~/.codex/AGENTS.md).
# The marker is also the uninstall handle: any line containing it is "ours".
MANAGED_MARKER    = "<!-- evolve-lite:managed -->"

# Codex cannot `@`-import another file, but it can be told to read one on
# demand. We drop a COPY of EVOLVE.md on disk and inject this single pointer
# line into ~/.codex/AGENTS.md instead of inlining the whole document.
CODEX_EVOLVE_MD_PATH = "~/.codex/evolve-lite/EVOLVE.md"

def _codex_pointer_line():
    return (
        "Evolve memory is active: at the start of every conversation, read "
        + CODEX_EVOLVE_MD_PATH + " and follow it — it governs recalling "
        "relevant past learnings and saving durable new ones. "
        + MANAGED_MARKER
    )


# Claude installs via marketplace (`claude plugin install`), which copies
# nothing to the repo and does NOT auto-load an ambient EVOLVE.md. So we drop a
# COPY of the thin EVOLVE.md at <repo>/.evolve/EVOLVE.md and inject a single
# native CLAUDE.md `@`-import line pointing at it. The path is repo-relative
# (resolves from CLAUDE.md's directory, i.e. repo root). The line is its own
# uninstall handle (the marker is a substring of the line) — no HTML comment.
CLAUDE_EVOLVE_MD_REL = ".evolve/EVOLVE.md"
CLAUDE_IMPORT_MARKER = CLAUDE_EVOLVE_MD_REL
CLAUDE_IMPORT_LINE   = "@" + CLAUDE_EVOLVE_MD_REL


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

    def remove_dir_if_empty(self, path):
        """Remove `path` only when it exists and contains nothing.

        Used to tidy up a per-plugin dir (e.g. ~/.bob/evolve-lite/) after its
        last managed file is removed, while leaving it intact if a user (or
        another plugin) dropped sibling content there."""
        path = str(path)
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
            debug(f"Removed empty dir: {path}")
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

        try:
            with open(target_yaml_path) as f:
                existing = f.read()
        except FileNotFoundError:
            existing = "customModes:\n"

        if not existing.strip() or "customModes:" not in existing:
            existing = "customModes:\n"

        # Match the list-item indentation already used under `customModes:` so the
        # inserted block doesn't mix 0-indent and 2-indent sequence items (which is
        # invalid YAML). The source uses 2-space items; a target written by
        # yaml.safe_dump (Bob/marketplace tooling) may use 0-space. Detect and match.
        item_indent = "  "
        seen_modes = False
        for ln in existing.splitlines():
            if ln.strip() == "customModes:":
                seen_modes = True
                continue
            if seen_modes and ln.lstrip().startswith("- "):
                item_indent = ln[: len(ln) - len(ln.lstrip())]
                break
        block_body = "\n".join(item_indent + ln if ln else ln for ln in mode_block.split("\n"))
        block = f"\n{start}\n{block_body}\n{end}\n"

        # Match a *real* sentinel block only: the start and end markers must each
        # sit at the beginning of a line. A bare sentinel substring inside another
        # mode's quoted scalar (e.g. the install-evolve-lite mode documents the
        # literal `# >>>evolve:evolve-lite<<<` in its customInstructions) must NOT
        # be treated as an existing block — otherwise the replace finds no matching
        # end, no-ops, and the merge is silently dropped while still reporting ✓.
        block_re = re.compile(
            r"^[ \t]*" + re.escape(start) + r".*?^[ \t]*" + re.escape(end) + r"[^\n]*$",
            re.DOTALL | re.MULTILINE,
        )
        if block_re.search(existing):
            new_content = block_re.sub(lambda _m: block.strip(), existing)
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
        # Line-anchored so a sentinel literal mentioned inside another mode's
        # quoted text is never mistaken for a real block (see merge above).
        pattern = re.compile(
            r"^[ \t]*" + re.escape(start) + r".*?" + re.escape(end) + r"[^\n]*$\n?",
            re.DOTALL | re.MULTILINE,
        )
        self.atomic_write_text(target_yaml_path, pattern.sub("", text))

    # ── Sentinel-block helpers (generic always-on instruction files) ───────────

    def inject_sentinel_block(self, path, slug, body):
        """Idempotently inject a sentinel-wrapped block into a text file.

        Writes:
            # >>>evolve:{slug}<<<
            {body}
            # <<<evolve:{slug}<<<

        If a block with the same sentinels already exists, it is replaced in
        place; otherwise the block is appended (separated by a blank line from
        any existing content). `body` is arbitrary text — a one-line `@`-import
        or a full multi-line document — and is never inspected here.
        """
        path = str(path)
        start = _sentinel_start(slug)
        end   = _sentinel_end(slug)
        block = f"{start}\n{body}\n{end}"

        try:
            with open(path) as f:
                existing = f.read()
        except FileNotFoundError:
            existing = ""

        # Match a real sentinel block only: start/end markers each anchored to
        # the beginning of a line (mirrors merge_yaml_custom_mode), so a literal
        # sentinel string quoted inside body content is never mistaken for one.
        block_re = re.compile(
            r"^[ \t]*" + re.escape(start) + r".*?^[ \t]*" + re.escape(end) + r"[^\n]*$",
            re.DOTALL | re.MULTILINE,
        )
        if block_re.search(existing):
            new_content = block_re.sub(lambda _m: block, existing)
        elif existing.strip():
            new_content = existing.rstrip() + "\n\n" + block + "\n"
        else:
            new_content = block + "\n"

        self.atomic_write_text(path, new_content)
        debug(f"Injected sentinel block '{slug}': {path}")

    def remove_sentinel_block(self, path, slug):
        """Remove the sentinel block for `slug` (and a trailing newline) if present."""
        path = str(path)
        if not os.path.isfile(path):
            return
        with open(path) as f:
            text = f.read()
        start = _sentinel_start(slug)
        end   = _sentinel_end(slug)
        pattern = re.compile(
            r"\n*^[ \t]*" + re.escape(start) + r".*?" + re.escape(end) + r"[^\n]*$\n?",
            re.DOTALL | re.MULTILINE,
        )
        self.atomic_write_text(path, pattern.sub("", text))
        debug(f"Removed sentinel block '{slug}': {path}")

    # ── Marker-line helpers (single greppable managed line) ────────────────────

    def inject_marker_line(self, path, marker, line):
        """Idempotently ensure a single managed `line` is present in `path`.

        `line` must contain `marker` (the uninstall handle). If an existing
        line in the file contains `marker`, that entire line is replaced with
        `line`; otherwise `line` is appended (preceded by a blank line when the
        file already has non-empty content). Creates the file/parents if
        missing. Atomic write.
        """
        path = str(path)
        if marker not in line:
            raise ValueError("inject_marker_line: line must contain marker")

        try:
            with open(path) as f:
                existing = f.read()
        except FileNotFoundError:
            existing = ""

        lines = existing.split("\n")
        replaced = False
        for i, ln in enumerate(lines):
            if marker in ln:
                lines[i] = line
                replaced = True
                break

        if replaced:
            new_content = "\n".join(lines)
        elif existing.strip():
            new_content = existing.rstrip("\n") + "\n\n" + line + "\n"
        else:
            new_content = line + "\n"

        self.atomic_write_text(path, new_content)
        debug(f"Injected marker line ({marker}): {path}")

    def remove_marker_line(self, path, marker):
        """Remove every line containing `marker` from `path`. No-op if missing.

        Avoids leaving a doubled blank line where the line used to be: when the
        removed line sat between two blank lines (or had a blank line before it
        and content after), the surrounding blank lines are collapsed.
        """
        path = str(path)
        if not os.path.isfile(path):
            return
        with open(path) as f:
            text = f.read()
        # Drop the managed line together with a single trailing newline, then
        # collapse any resulting run of 3+ newlines down to a paragraph break.
        pattern = re.compile(r"^.*" + re.escape(marker) + r".*$\n?", re.MULTILINE)
        new_text = pattern.sub("", text)
        new_text = re.sub(r"\n{3,}", "\n\n", new_text)
        # Tidy a trailing blank-line gap left behind at EOF.
        new_text = new_text.rstrip("\n")
        if text.endswith("\n") and new_text:
            new_text += "\n"
        self.atomic_write_text(path, new_text)
        debug(f"Removed marker line ({marker}): {path}")


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

    def remove_dir_if_empty(self, path):
        dryrun(f"remove dir if empty → {path}")
        return True

    def run_subprocess(self, cmd_list):
        dryrun(f"run: {' '.join(cmd_list)}")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def merge_yaml_custom_mode(self, source_yaml_path, target_yaml_path, slug):
        dryrun(f"merge YAML custom mode '{slug}' → {target_yaml_path}")

    def inject_sentinel_block(self, path, slug, body):
        dryrun(f"inject sentinel block '{slug}' → {path}")

    def remove_sentinel_block(self, path, slug):
        dryrun(f"remove sentinel block '{slug}' → {path}")

    def inject_marker_line(self, path, marker, line):
        dryrun(f"inject marker line ({marker}) → {path}")

    def remove_marker_line(self, path, marker):
        dryrun(f"remove marker line ({marker}) → {path}")


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
        "claw-code": (
            shutil.which("claw") is not None or
            (target / ".claw").is_dir()
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

    def _purge_evolve_artifacts(self, bob_target):
        """Remove every evolve-prefixed skill, command, and directory under bob_target.

        Catches both the current `evolve-lite-<name>` dash form and the legacy
        `evolve-lite:<name>` colon form (plus any future `evolve-*` namespace),
        so re-running install over an older layout converges to a clean state
        instead of accumulating duplicates. User-owned non-evolve content
        (`my-custom-skill/`, `my-command.md`, …) is preserved.
        """
        skills_dir = bob_target / "skills"
        if skills_dir.is_dir():
            for entry in sorted(skills_dir.iterdir()):
                if entry.is_dir() and entry.name.startswith("evolve"):
                    self.ops.remove_dir(entry)
        commands_dir = bob_target / "commands"
        if commands_dir.is_dir():
            for entry in sorted(commands_dir.iterdir()):
                if entry.is_file() and entry.name.startswith("evolve"):
                    self.ops.remove_file(entry)
        if bob_target.is_dir():
            for entry in sorted(bob_target.iterdir()):
                if entry.is_dir() and entry.name.startswith("evolve"):
                    self.ops.remove_dir(entry)
        # The shared lib now renders under lib/evolve-lite/ (namespaced so
        # plugins can co-locate in lib/). It is no longer an evolve-prefixed
        # top-level dir, so the loop above misses it — remove our subdir
        # explicitly, leaving the shared lib/ parent and any sibling
        # lib/<other-plugin>/ intact.
        lib_dir = bob_target / "lib" / "evolve-lite"
        if lib_dir.is_dir():
            self.ops.remove_dir(lib_dir)

    def _modes_file(self, bob_target):
        """Resolve where Bob *reads* custom modes for this target.

        Bob's config is asymmetric: it loads global modes from
        ``<home>/.bob/settings/custom_modes.yaml`` but project-scoped modes
        from ``<project>/.bob/custom_modes.yaml`` (bob bundle: the global
        reader joins ``settings/``, the workspace reader does not). Writing to
        the wrong one installs a mode Bob never loads, so mirror Bob's split.
        """
        if bob_target.resolve() == (Path.home() / ".bob").resolve():
            return bob_target / "settings" / "custom_modes.yaml"
        return bob_target / "custom_modes.yaml"

    def _mcp_file(self, bob_target):
        """Global MCP settings live at ``<home>/.bob/settings/mcp_settings.json``;
        project scope keeps ``<project>/.bob/mcp.json``."""
        if bob_target.resolve() == (Path.home() / ".bob").resolve():
            return bob_target / "settings" / "mcp_settings.json"
        return bob_target / "mcp.json"

    def _rules_file(self):
        """Resolve Bob's GLOBAL custom-instructions rules file.

        Bob loads every ``~/.bob/rules/*.md`` into every session, globally,
        ungated and mode-independent, as the user's custom instructions. The
        lite installer owns ``00-evolve-lite.md`` entirely — it is always
        global regardless of install scope, never a project file."""
        return Path.home() / ".bob" / "rules" / BOB_RULES_FILE

    def _audit_script_file(self):
        """Resolve Bob's GLOBAL recall-audit script path.

        EVOLVE.md tells the model to run ``python3 ~/.bob/evolve-lite/audit_recall.py``
        after recall. The script is installed once, globally, regardless of
        install scope (matching the always-global rules file), so the absolute
        path baked into the instructions always resolves."""
        return Path.home() / ".bob" / "evolve-lite" / AUDIT_SCRIPT

    def install(self, target_dir, mode="lite"):
        _ensure_source_dir()
        source_dir = SOURCE_DIR
        bob_source_lite = Path(source_dir) / "platform-integrations" / "bob" / "evolve-lite"
        bob_target = Path(target_dir) / ".bob"
        modes_file = self._modes_file(bob_target)

        info(f"Installing Bob ({mode} mode) → {bob_target}")

        # Wipe any existing evolve-prefixed artifacts (legacy colon-form
        # skills/commands from before the rename, stale evolve-lib dirs,
        # etc.) before re-rendering. Without this, re-running install
        # over an old layout would leave duplicate `evolve-lite:<name>`
        # alongside the new `evolve-lite-<name>`.
        self._purge_evolve_artifacts(bob_target)

        if mode == "lite":
            shared_lib = bob_source_lite / "lib" / "evolve-lite"
            if not self.ops.is_dry_run and not shared_lib.is_dir():
                raise RuntimeError(f"Shared lib not found: {shared_lib}")
            self.ops.copy_tree(shared_lib, bob_target / "lib" / "evolve-lite")
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

            # Always-on instructions: write the full EVOLVE.md text to Bob's
            # GLOBAL rules dir. Bob loads every `~/.bob/rules/*.md` into every
            # session, ungated and mode-independent, as the user's custom
            # instructions. This is always global regardless of install scope.
            # The installer owns this file entirely — overwrite, no merging.
            evolve_src = bob_source_lite / "EVOLVE.md"
            if not self.ops.is_dry_run and not evolve_src.is_file():
                evolve_src = Path(source_dir) / "plugin-source" / "EVOLVE.md"
            rules_file = self._rules_file()
            if not self.ops.is_dry_run:
                self.ops.atomic_write_text(rules_file, evolve_src.read_text())
            else:
                self.ops.atomic_write_text(rules_file, "")
            success(f"Wrote always-on instructions → {rules_file}")

            # Recall-audit script: EVOLVE.md tells the model to run
            # `python3 ~/.bob/evolve-lite/audit_recall.py` after recall, so
            # install the script once at that GLOBAL absolute path (matching
            # the always-global rules file). Prefer the rendered bob copy;
            # fall back to the shared plugin-source original.
            audit_src = bob_source_lite / "scripts" / AUDIT_SCRIPT
            if not self.ops.is_dry_run and not audit_src.is_file():
                audit_src = Path(source_dir) / "plugin-source" / "scripts" / AUDIT_SCRIPT
            audit_file = self._audit_script_file()
            if not self.ops.is_dry_run:
                self.ops.atomic_write_text(audit_file, audit_src.read_text())
            else:
                self.ops.atomic_write_text(audit_file, "")
            success(f"Installed recall-audit script → {audit_file}")

        elif mode == "full":
            bob_source_full = Path(source_dir) / "platform-integrations" / "bob" / "evolve-full"
            mcp_source = bob_source_full / "mcp.json"
            if not self.ops.is_dry_run and not mcp_source.exists():
                raise RuntimeError(f"Source MCP config not found: {mcp_source}")
            mcp_file = self._mcp_file(bob_target)
            if not self.ops.is_dry_run:
                mcp_data = read_json(mcp_source)
                self.ops.upsert_json_key(mcp_file, ["mcpServers", "evolve"], mcp_data["mcpServers"]["evolve"])
            else:
                self.ops.upsert_json_key(mcp_file, ["mcpServers", "evolve"], {})
            success(f"Upserted MCP server config in {mcp_file}")

            self.ops.merge_yaml_custom_mode(
                bob_source_full / "custom_modes.yaml",
                modes_file,
                "Evolve",
            )
            success(f"Merged custom mode 'Evolve' into {modes_file}")

        success("Bob installation complete")

    def uninstall(self, target_dir):
        bob_target = Path(target_dir) / ".bob"
        info(f"Uninstalling Bob from {bob_target}")

        self._purge_evolve_artifacts(bob_target)
        # Lite: drop the global always-on instructions rules file and the
        # recall-audit script (and its dir if nothing else lives there).
        self.ops.remove_file(self._rules_file())
        audit_file = self._audit_script_file()
        self.ops.remove_file(audit_file)
        self.ops.remove_dir_if_empty(audit_file.parent)
        # Full: remove the 'Evolve' custom mode (scope-correct *and* legacy
        # top-level file) and the MCP server entry. A stale BOB_SLUG custom mode
        # from a pre-redesign lite install is also swept up here.
        modes_files = {self._modes_file(bob_target), bob_target / "custom_modes.yaml"}
        for mf in modes_files:
            self.ops.remove_yaml_custom_mode(mf, BOB_SLUG)
            self.ops.remove_yaml_custom_mode(mf, "Evolve")
        for mcpf in {self._mcp_file(bob_target), bob_target / "mcp.json"}:
            self.ops.remove_json_key(mcpf, ["mcpServers", "evolve"])

        success("Bob uninstall complete")

    def status(self, target_dir):
        bob_target = Path(target_dir) / ".bob"
        print(f"  Bob (.bob/):")
        print(f"    lib/evolve-lite/entity_io : {'✓' if (bob_target / 'lib' / 'evolve-lite' / 'entity_io.py').is_file() else '✗'}")
        skills_dir = bob_target / "skills"
        # Glob `evolve*` rather than `evolve-lite-*` so legacy colon-form
        # skills (`evolve-lite:learn` etc.) show up in status; otherwise
        # an upgrade-gap state would silently report ✗ while artifacts
        # still squat on disk.
        installed_skills = sorted(p for p in skills_dir.glob("evolve*") if p.is_dir()) if skills_dir.is_dir() else []
        if installed_skills:
            for s in installed_skills:
                print(f"    skills/{s.name} : ✓")
        else:
            print(f"    skills/evolve*            : ✗")
        commands_dir = bob_target / "commands"
        installed_cmds = sorted(commands_dir.glob("evolve*.md")) if commands_dir.is_dir() else []
        print(f"    commands/ ({len(installed_cmds)} evolve commands) : {'✓' if installed_cmds else '✗'}")
        rules_file = self._rules_file()
        print(f"    rules/{BOB_RULES_FILE}    : {'✓' if rules_file.is_file() else '✗'}")
        audit_file = self._audit_script_file()
        print(f"    evolve-lite/{AUDIT_SCRIPT} : {'✓' if audit_file.is_file() else '✗'}")
        modes_file = self._modes_file(bob_target)
        modes_rel = str(modes_file.relative_to(bob_target))
        print(f"    {modes_rel:<25} : {'✓ (full mode)' if modes_file.is_file() else '✗'}")
        mcp_file = self._mcp_file(bob_target)
        has_mcp = "evolve" in read_json(mcp_file).get("mcpServers", {}) if mcp_file.is_file() else False
        print(f"    mcp (full mode)           : {'✓' if has_mcp else '✗'}")


# ── Claude ────────────────────────────────────────────────────────────────────

class ClaudeInstaller:
    def __init__(self, ops: FileOps):
        self.ops = ops

    def _deliver_files(self, target_dir):
        """Per-repo file delivery (independent of the `claude` CLI).

        Claude installs the plugin via marketplace, which copies nothing to the
        repo and does NOT auto-load an ambient EVOLVE.md. So we deliver the thin
        EVOLVE.md ourselves: drop a COPY at <repo>/.evolve/EVOLVE.md and inject a
        single native `@`-import pointer line into <repo>/CLAUDE.md, exactly as
        CodexInstaller injects its pointer into ~/.codex/AGENTS.md. Kept as a
        separate method so it is exercisable in tests without the real CLI.
        """
        _ensure_source_dir()
        source_dir = SOURCE_DIR
        plugin_source = Path(source_dir) / "platform-integrations" / "claude" / "plugins" / CLAUDE_PLUGIN

        # Drop a COPY of the thin EVOLVE.md at <repo>/.evolve/EVOLVE.md. Prefer
        # the rendered claude plugin copy; fall back to the shared original.
        evolve_src = plugin_source / "EVOLVE.md"
        if not evolve_src.is_file():
            evolve_src = Path(source_dir) / "plugin-source" / "EVOLVE.md"
        evolve_text = "" if self.ops.is_dry_run and not evolve_src.is_file() else evolve_src.read_text()
        evolve_dst = Path(target_dir) / CLAUDE_EVOLVE_MD_REL
        self.ops.atomic_write_text(evolve_dst, evolve_text)
        success(f"Copied EVOLVE.md → {evolve_dst}")

        # Inject the single native `@`-import pointer line into <repo>/CLAUDE.md.
        # The path resolves relative to CLAUDE.md (repo root). The line is its
        # own uninstall handle (marker is a substring of the line).
        claude_md = Path(target_dir) / "CLAUDE.md"
        self.ops.inject_marker_line(claude_md, CLAUDE_IMPORT_MARKER, CLAUDE_IMPORT_LINE)
        success(f"Injected '{CLAUDE_PLUGIN}' import pointer into {claude_md}")
        if self.ops.is_dry_run:
            dryrun("Claude shows a one-time 'allow external imports' dialog on first session")
        else:
            warn(
                "On the first Claude session in this repo, an 'allow external "
                "imports' dialog will appear — you must Allow it, or the "
                f"{CLAUDE_IMPORT_LINE} import is silently disabled."
            )

        # Recall-audit script: the thin EVOLVE.md instructs running
        # `~/.claude/evolve-lite/audit_recall.py`, so install it at that GLOBAL
        # absolute path (mirroring CodexInstaller). Prefer the rendered claude
        # copy; fall back to the shared plugin-source original.
        audit_src = plugin_source / "scripts" / AUDIT_SCRIPT
        if not audit_src.is_file():
            audit_src = Path(source_dir) / "plugin-source" / "scripts" / AUDIT_SCRIPT
        audit_text = "" if self.ops.is_dry_run and not audit_src.is_file() else audit_src.read_text()
        audit_file = Path.home() / ".claude" / "evolve-lite" / AUDIT_SCRIPT
        self.ops.atomic_write_text(audit_file, audit_text)
        success(f"Installed recall-audit script → {audit_file}")

    def install(self, target_dir):
        info("Installing Claude plugin via marketplace")

        # Deliver the per-repo EVOLVE.md + import pointer + global audit script
        # regardless of whether the `claude` CLI is present below.
        self._deliver_files(target_dir)

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

        # Drop the single managed `@`-import pointer line from <repo>/CLAUDE.md,
        # remove the per-repo EVOLVE.md copy we placed (NOT the whole .evolve/
        # store), and remove the global recall-audit script (mirrors Codex).
        self.ops.remove_marker_line(Path(target_dir) / "CLAUDE.md", CLAUDE_IMPORT_MARKER)
        self.ops.remove_file(Path(target_dir) / CLAUDE_EVOLVE_MD_REL)
        claude_evolve_dir = Path.home() / ".claude" / "evolve-lite"
        self.ops.remove_file(claude_evolve_dir / AUDIT_SCRIPT)
        self.ops.remove_dir_if_empty(claude_evolve_dir)

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


# ── Claw Code ─────────────────────────────────────────────────────────────────

class ClawCodeInstaller:
    def __init__(self, ops: FileOps):
        self.ops = ops

    def install(self, target_dir):
        _ensure_source_dir()
        plugin_source = Path(SOURCE_DIR) / "platform-integrations" / "claw-code" / "plugins" / CLAW_CODE_PLUGIN
        info(f"Installing Claw Code plugin from {plugin_source}")

        claw = shutil.which("claw")
        if not claw:
            warn("Claw CLI not found on PATH. To install manually, run:")
            print()
            print(f"    claw plugins install {plugin_source.resolve()}")
            print()
            return

        result = self.ops.run_subprocess([claw, "plugins", "install", str(plugin_source.resolve())])
        if result.returncode == 0:
            if self.ops.is_dry_run:
                dryrun("Claw Code plugin would be installed via CLI")
            else:
                success("Claw Code plugin installed via CLI")
        else:
            warn(f"claw plugins install exited with code {result.returncode}")
            warn("To install manually, run:")
            print()
            print(f"    claw plugins install {plugin_source.resolve()}")
            print()

    def uninstall(self, target_dir):
        info("Uninstalling Claw Code plugin")
        claw = shutil.which("claw")
        if not claw:
            warn("Could not uninstall Claw Code plugin automatically.")
            warn(f"Run manually: claw plugins uninstall {CLAW_CODE_PLUGIN}@external")
            return

        result = self.ops.run_subprocess([claw, "plugins", "uninstall", f"{CLAW_CODE_PLUGIN}@external"])
        if result.returncode == 0:
            success("Claw Code plugin uninstalled via CLI")
        else:
            warn(f"claw plugins uninstall exited with code {result.returncode}")
            warn(f"Run manually: claw plugins uninstall {CLAW_CODE_PLUGIN}@external")

    def status(self, target_dir):
        print(f"  Claw Code:")
        claw = shutil.which("claw")
        if not claw:
            print(f"    claw CLI            : ✗ (not found on PATH)")
            return
        print(f"    claw CLI            : ✓")
        try:
            result = subprocess.run([claw, "plugins", "list"], capture_output=True, text=True)
            installed = CLAW_CODE_PLUGIN in result.stdout
            print(f"    evolve-lite plugin  : {'✓' if installed else '✗ (not installed)'}")
        except Exception:
            print(f"    evolve-lite plugin  : ? (could not query)")


# ── Codex ─────────────────────────────────────────────────────────────────────

class CodexInstaller:
    def __init__(self, ops: FileOps):
        self.ops = ops

    # ── Codex marketplace schema helpers ──────────────────────────────────────

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

    # ── Public interface ──────────────────────────────────────────────────────

    def install(self, target_dir):
        _ensure_source_dir()
        source_dir = SOURCE_DIR
        plugin_source = Path(source_dir) / "platform-integrations" / "codex" / "plugins" / CODEX_PLUGIN
        plugin_target = Path(target_dir) / "plugins" / CODEX_PLUGIN
        info(f"Installing Codex → {plugin_target}")

        self.ops.copy_tree(plugin_source, plugin_target)
        success("Copied Codex plugin")

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

        # Always-on instructions: Codex reads ~/.codex/AGENTS.md verbatim and
        # does NOT support `@`-imports. So we drop a COPY of EVOLVE.md on disk
        # and inject a single greppable pointer line into AGENTS.md telling the
        # agent to read that file on demand. Prefer the rendered codex copy;
        # fall back to the shared plugin-source original.
        evolve_src = plugin_source / "EVOLVE.md"
        if not evolve_src.is_file():
            evolve_src = Path(source_dir) / "plugin-source" / "EVOLVE.md"
        evolve_text = "" if self.ops.is_dry_run and not evolve_src.is_file() else evolve_src.read_text()
        evolve_dst = Path.home() / ".codex" / "evolve-lite" / "EVOLVE.md"
        self.ops.atomic_write_text(evolve_dst, evolve_text)
        success(f"Copied EVOLVE.md → {evolve_dst}")

        agents_file = Path.home() / ".codex" / "AGENTS.md"
        self.ops.inject_marker_line(agents_file, MANAGED_MARKER, _codex_pointer_line())
        success(f"Injected '{CODEX_PLUGIN}' pointer into {agents_file}")

        # Recall-audit script: the injected AGENTS.md block tells the model to
        # run `python3 ~/.codex/evolve-lite/audit_recall.py` after recall, so
        # install the script at that GLOBAL absolute path (matching how the
        # always-on instructions live globally). Prefer the rendered codex
        # copy; fall back to the shared plugin-source original.
        audit_src = plugin_source / "scripts" / AUDIT_SCRIPT
        if not audit_src.is_file():
            audit_src = Path(source_dir) / "plugin-source" / "scripts" / AUDIT_SCRIPT
        audit_text = "" if self.ops.is_dry_run and not audit_src.is_file() else audit_src.read_text()
        audit_file = Path.home() / ".codex" / "evolve-lite" / AUDIT_SCRIPT
        self.ops.atomic_write_text(audit_file, audit_text)
        success(f"Installed recall-audit script → {audit_file}")

        success("Codex installation complete")

    def uninstall(self, target_dir):
        info(f"Uninstalling Codex from {target_dir}")

        self.ops.remove_dir(Path(target_dir) / "plugins" / CODEX_PLUGIN)
        self.ops.remove_json_array_item(
            Path(target_dir) / ".agents" / "plugins" / "marketplace.json",
            "plugins", "name", CODEX_PLUGIN,
        )
        # Drop the single managed pointer line from the always-on instructions.
        self.ops.remove_marker_line(Path.home() / ".codex" / "AGENTS.md", MANAGED_MARKER)
        # Remove the on-disk EVOLVE.md copy and the recall-audit script, then the
        # per-plugin dir if nothing else lives there.
        evolve_dir = Path.home() / ".codex" / "evolve-lite"
        self.ops.remove_file(evolve_dir / "EVOLVE.md")
        self.ops.remove_file(evolve_dir / AUDIT_SCRIPT)
        self.ops.remove_dir_if_empty(evolve_dir)

        success("Codex uninstall complete")

    def status(self, target_dir):
        plugin_dir = Path(target_dir) / "plugins" / CODEX_PLUGIN
        print("  Codex:")
        print(f"    plugins/evolve-lite       : {'✓' if plugin_dir.is_dir() else '✗'}")
        print(f"    lib/evolve-lite/entity_io : {'✓' if (plugin_dir / 'lib' / 'evolve-lite' / 'entity_io.py').is_file() else '✗'}")
        print(f"    skills/evolve-lite/learn  : {'✓' if (plugin_dir / 'skills' / 'evolve-lite' / 'learn').is_dir() else '✗'}")
        print(f"    skills/evolve-lite/recall : {'✓' if (plugin_dir / 'skills' / 'evolve-lite' / 'recall').is_dir() else '✗'}")

        marketplace_path = Path(target_dir) / ".agents" / "plugins" / "marketplace.json"
        marketplace_present = (
            any(p.get("name") == CODEX_PLUGIN for p in read_json(marketplace_path).get("plugins", []))
            if marketplace_path.is_file() else False
        )
        print(f"    marketplace.json entry    : {'✓' if marketplace_present else '✗'}")

        agents_path = Path.home() / ".codex" / "AGENTS.md"
        pointer_present = (
            any(MANAGED_MARKER in ln for ln in agents_path.read_text().splitlines())
            if agents_path.is_file() else False
        )
        print(f"    ~/.codex/AGENTS.md pointer : {'✓' if pointer_present else '✗'}")

        evolve_md = Path.home() / ".codex" / "evolve-lite" / "EVOLVE.md"
        print(f"    evolve-lite/EVOLVE.md     : {'✓' if evolve_md.is_file() else '✗'}")

        audit_file = Path.home() / ".codex" / "evolve-lite" / AUDIT_SCRIPT
        print(f"    evolve-lite/{AUDIT_SCRIPT} : {'✓' if audit_file.is_file() else '✗'}")


# ── Dispatch ──────────────────────────────────────────────────────────────────

PLATFORM_CLASSES = {
    "bob":       BobInstaller,
    "claude":    ClaudeInstaller,
    "claw-code": ClawCodeInstaller,
    "codex":     CodexInstaller,
}


def cmd_install(args):
    target_dir = os.path.abspath(args.dir)
    ops = DryRunFileOps() if DRY_RUN else FileOps()

    if args.platform == "all":
        platforms = ["bob", "claude", "claw-code", "codex"]
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
        platforms = ["bob", "claude", "claw-code", "codex"]
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
    ClawCodeInstaller(ops).status(target_dir)
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
        "--platform", choices=["bob", "claude", "claw-code", "codex", "all"], default=None,
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
        "--platform", choices=["bob", "claude", "claw-code", "codex", "all"], default=None,
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
