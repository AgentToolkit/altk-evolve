#!/usr/bin/env python3
"""End-to-end smoke test for evolve-lite skills against real host CLIs.

Exercises /evolve-lite:learn, /evolve-lite:recall, and /evolve-lite:publish
through the real claude, codex, and bob CLIs in a single throwaway
workspace installed via the project's own `platform-integrations/install.sh`
in `--platform all --mode lite` mode — exactly the way a real user installs.
This drives actual model invocations: expect time and API cost on each run.

Filename intentionally does not start with `test_` so pytest will not
collect it. Run directly:

    python3 tests/smoke_skills.py                   # all three platforms
    python3 tests/smoke_skills.py --platform codex  # one platform
    python3 tests/smoke_skills.py --keep            # leave tempdir on exit

Assumptions (per the task brief):
  - claude, codex, bob CLIs are installed and already authenticated for
    the invoking user. The script does not configure auth.
  - For claude and codex, learn is exercised against a *real* seed
    task and the pass criterion is "entity count grew above baseline",
    not just "exit 0". The chain mechanism differs:
      * claude — Stop hooks (save-trajectory + learn) auto-fire after
        the seed; learn runs in a forked sub-agent and reads the saved
        trajectory off disk.
      * codex — no Stop hooks for this; instead the seed prompt
        is suffixed with "When done, run <learn-slash-cmd>." so the
        same session invokes learn at the end. Learn runs in main
        context on codex (build_plugins.py only sets `forked_context`
        for claude), so it sees the live conversation directly without
        needing a saved trajectory.
      * bob — skill execution is currently DISABLED. Bob's slash-command
        parser ignores slash commands embedded in mid-response assistant
        text, so a single seed-and-learn prompt can't drive the chain;
        and `bob --resume latest`, which would let us send slash commands
        as fresh user messages, is broken upstream. Until --resume is
        fixed we verify bob's install presence only and skip learn,
        recall, and publish. The bob row in the summary will read
        "install-only" and PASS so long as the SKILL.md files landed
        in the workspace.

Side-effects to be aware of:
  - codex and bob installs are project-local under the workspace (per
    install.sh's design — codex writes plugins/, .agents/, .codex/;
    bob writes .bob/), so the tempdir wipe is the only cleanup needed.
  - claude is the odd one out: install.sh's claude path uses CLI install
    (`claude plugin marketplace add` + `claude plugin install`) which
    mutates the user's real ~/.claude/ and would clobber any
    pre-existing evolve-lite install they have. We side-step that by
    using install.sh's own documented manual fallback (`claude
    --plugin-dir <local repo path>`), so claude never touches global
    state during the smoke run. The user's day-to-day claude install is
    untouched.

Out of scope: claw-code; CI; release gating.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
PLATFORM_INTEGRATIONS = REPO_ROOT / "platform-integrations"
INSTALL_SH = PLATFORM_INTEGRATIONS / "install.sh"
CLAUDE_PLUGIN_DIR = PLATFORM_INTEGRATIONS / "claude" / "plugins" / "evolve-lite"

PLATFORMS = ("claude", "codex", "bob")

# Skill timing ceiling. The real LLM round-trip can be slow; if a single
# invocation exceeds this, we treat the platform as failed and move on.
PER_INVOCATION_TIMEOUT_SECONDS = 300


# ─── logging ──────────────────────────────────────────────────────────────────
#
# Two output modes, auto-selected based on whether stdout is a TTY:
#
#   * Live mode (TTY, multi-platform): LiveGroupedHandler keeps a buffer of
#     records per thread name (= platform) and redraws the whole region with
#     ANSI cursor control on every new record. Lines stay grouped by platform
#     and chronologically ordered within a group, so a human sees structured,
#     real-time progress for all three platforms at once.
#
#   * Line mode (non-TTY: piped, captured by an agent, redirected to a file):
#     plain stdlib StreamHandler with `[%(threadName)-7s] %(message)s` format.
#     Each line is independently greppable, no escape codes pollute captured
#     output. This is the mode that runs when an automation/CI/agent pipes
#     this script's stdout.
#
# A `--no-live` flag forces line mode in a TTY for debugging.

logger = logging.getLogger("smoke")


class LiveGroupedHandler(logging.Handler):
    """Redraws all log records grouped per-thread on every new record.

    Each thread (= platform) gets a section under a `── claude ──` header
    with its lines in arrival order. On `emit`, the entire managed region
    is rewritten via ANSI cursor controls so old lines don't shift on
    top of new ones. Calls are serialized through a lock so concurrent
    threads can't race on cursor positioning.

    Records logged before any worker thread starts (i.e. from MainThread)
    appear under their own group at the top — useful for the `tempdir: ...`
    line and any cleanup messages.

    Once `finalize()` is called, the handler stops redrawing; further
    records print as plain prefixed lines below the final region. This
    lets the summary print cleanly underneath at the end of a run.
    """

    # Defensive cap: if a single platform produces more than this many
    # lines, drop the oldest (with a `…(N earlier lines elided)` notice).
    # Smoke output is small enough that this rarely trips, but it keeps
    # the redraw region under typical terminal heights and avoids the
    # "cursor up N lines went past the scrollback start" corruption.
    MAX_LINES_PER_GROUP = 12

    def __init__(self, group_order: tuple[str, ...]) -> None:
        super().__init__()
        self._order: list[str] = list(group_order)
        self._groups: dict[str, list[str]] = {}
        self._dropped: dict[str, int] = {}
        self._lock = threading.Lock()
        self._last_lines = 0
        self._finalized = False

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover (TTY)
        try:
            line = self.format(record)
            group = record.threadName or "main"
            with self._lock:
                if self._finalized:
                    # Post-finalize records: plain append with prefix below
                    # the region we used to manage.
                    sys.stdout.write(f"[{group}] {line}\n")
                    sys.stdout.flush()
                    return
                if group not in self._groups:
                    self._groups[group] = []
                    self._dropped[group] = 0
                    if group not in self._order:
                        self._order.append(group)
                self._groups[group].append(line)
                excess = len(self._groups[group]) - self.MAX_LINES_PER_GROUP
                if excess > 0:
                    self._groups[group] = self._groups[group][excess:]
                    self._dropped[group] += excess
                self._render()
        except Exception:
            self.handleError(record)

    def _render(self) -> None:
        out: list[str] = []
        if self._last_lines:
            # Move cursor to column 1 of the line `_last_lines` lines up,
            # then erase from cursor to end of screen — wipes the previously
            # drawn region so we redraw cleanly.
            out.append(f"\033[{self._last_lines}F\033[0J")
        new_lines = 0
        for group in self._order:
            lines = self._groups.get(group)
            if not lines:
                continue
            out.append(f"── {group} ──\n")
            new_lines += 1
            if self._dropped.get(group):
                out.append(f"  …({self._dropped[group]} earlier lines elided)\n")
                new_lines += 1
            for line in lines:
                out.append(line + "\n")
                new_lines += 1
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self._last_lines = new_lines

    def finalize(self) -> None:
        """Stop redrawing; future records print as plain prefixed lines."""
        with self._lock:
            if self._finalized:
                return
            self._finalized = True
            # Newline so the next print() (e.g. summary) starts cleanly
            # below the last drawn region instead of overwriting it.
            sys.stdout.write("\n")
            sys.stdout.flush()


def setup_logging(verbose: bool, live: bool, group_order: tuple[str, ...]) -> logging.Handler:
    if live:
        handler: logging.Handler = LiveGroupedHandler(group_order)
        # Live mode renders the threadName as a section header, so the
        # message itself doesn't need to repeat it.
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(threadName)-7s] %(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    return handler


def section(msg: str) -> None:
    """Banner line. Goes through logging so it lands in the right group."""
    bar = "─" * max(0, 78 - len(msg) - 4)
    logger.info(f"── {msg} {bar}")


# ─── per-platform result record ───────────────────────────────────────────────


@dataclass
class SkillResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class PlatformResult:
    platform: str
    skipped_reason: str | None = None
    setup_error: str | None = None
    skills: list[SkillResult] = field(default_factory=list)
    log_path: Path | None = None

    @property
    def overall_ok(self) -> bool:
        if self.skipped_reason or self.setup_error:
            return False
        return all(s.ok for s in self.skills)


# ─── shared tempdir + cleanup ─────────────────────────────────────────────────


def make_root_tempdir() -> Path:
    root = Path(tempfile.mkdtemp(prefix="evolve-smoke-"))
    logger.info(f"tempdir: {root}")
    return root


def setup_isolated_codex_home(workspace: Path) -> Path:
    """Create a tempdir-scoped CODEX_HOME and copy the user's auth.

    Codex caches plugins under $CODEX_HOME/plugins/cache/. Pointing
    CODEX_HOME at a workspace-local path keeps every byte of plugin
    state — cache, memories, logs, config — under the tempdir so it
    gets wiped with the smoke run, no user-home pollution.

    auth.json + installation_id are copied from ~/.codex so the run
    stays authenticated. config.toml is NOT inherited from the user's
    home — register_codex_plugin() writes a fresh one with the entries
    needed for `$evolve-lite:<skill>` to resolve.
    """
    codex_home = workspace / ".codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    user_codex = Path.home() / ".codex"
    for fname in ("auth.json", "installation_id"):
        src = user_codex / fname
        if src.is_file():
            shutil.copy2(src, codex_home / fname)
    return codex_home


def register_codex_plugin(workspace: Path) -> None:
    """Replicate what codex's interactive `/plugin install` does, headless.

    Must be called AFTER install.sh has written the workspace plugin tree
    (it reads from workspace/plugins/evolve-lite/) and AFTER
    setup_isolated_codex_home() has created the workspace's CODEX_HOME.

    Three steps are required for `$evolve-lite:<skill>` invocations to
    resolve via codex's skill registry (proven the hard way — see the
    long codex-registration investigation in this file's git history):

      1. **Marketplace registered** via `codex plugin marketplace add
         <workspace>`. install.sh writes
         `<workspace>/.agents/plugins/marketplace.json` but that's
         metadata — codex still needs the explicit `marketplace add` to
         record `[marketplaces.evolve-local]` in CODEX_HOME's config.toml.

      2. **Plugin cache populated** at
         `<CODEX_HOME>/plugins/cache/evolve-local/evolve-lite/<version>/`
         with a FLAT skills layout (`skills/<name>/SKILL.md`, NOT nested
         under `skills/evolve-lite/<name>/`). Codex's plugin loader walks
         `<cache>/skills/<*>/SKILL.md` and ignores plugin.json's `skills`
         path field — so we have to flatten on copy even though the
         source tree nests skills under `skills/evolve-lite/<name>/` for
         claude's runtime convention.

      3. **Plugin enabled in PERSISTED config.toml**, not the `-c
         plugins."x@y".enabled=true` per-invocation flag. The flag form
         doesn't trigger codex's startup plugin-discovery pass; only the
         persisted entry does.

    There is no `codex plugin install` CLI subcommand (only interactive
    TUI). Steps 2 and 3 are what `/plugin install evolve-lite@evolve-local`
    does manually; we replicate them here for headless runs.
    """
    codex_home = workspace / ".codex-home"

    # Step 1: register the workspace as a local marketplace. Writes
    # [marketplaces.evolve-local] into CODEX_HOME's config.toml.
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    subprocess.run(
        ["codex", "plugin", "marketplace", "add", str(workspace)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    # Step 2: populate the plugin cache with a FLAT skills layout.
    plugin_src = workspace / "plugins" / "evolve-lite"
    plugin_json = plugin_src / ".codex-plugin" / "plugin.json"
    version = json.loads(plugin_json.read_text(encoding="utf-8")).get("version", "0.0.0")
    cache_dir = codex_home / "plugins" / "cache" / "evolve-local" / "evolve-lite" / version
    cache_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plugin_src / ".codex-plugin", cache_dir / ".codex-plugin", dirs_exist_ok=True)
    shutil.copytree(plugin_src / "lib", cache_dir / "lib", dirs_exist_ok=True)
    # Flatten: source has skills/evolve-lite/<name>/, cache wants skills/<name>/.
    shutil.copytree(plugin_src / "skills" / "evolve-lite", cache_dir / "skills", dirs_exist_ok=True)

    # Step 3: persist [plugins."evolve-lite@evolve-local"] in config.toml.
    # Append — `marketplace add` already wrote [marketplaces.evolve-local].
    config_toml = codex_home / "config.toml"
    with config_toml.open("a", encoding="utf-8") as f:
        f.write('\n[plugins."evolve-lite@evolve-local"]\nenabled = true\n')


def cleanup_claude_projects(workspace: Path) -> None:
    """Remove ~/.claude/projects/<encoded-workspace>/ trees from this run.

    Claude persists per-cwd state — session transcripts AND auto-memory
    writes — under ~/.claude/projects/<encoded-cwd>/. The encoding is
    fiddly: macOS resolves /var → /private/var via realpath, and claude
    also normalizes `_` to `-` in path components. Rather than replicate
    those rules, we match by the unique tempdir suffix
    (e.g. `evolve-smoke-XXXXXXXX-workspace`) which appears verbatim in the
    encoded directory regardless of upstream normalization. Each smoke run
    uses a fresh tempdir, so this is unambiguously scoped to one run.

    This also takes care of cleaning up any auto-memory entries the seed
    session may have written when it saw the word 'Remember' in the prompt
    — those land under <encoded-cwd>/memory/, which is part of the subtree
    we're deleting.
    """
    suffix = f"{workspace.parent.name}-{workspace.name}"
    base = Path.home() / ".claude" / "projects"
    if not base.is_dir():
        return
    for entry in base.iterdir():
        if entry.is_dir() and suffix in entry.name:
            shutil.rmtree(entry, ignore_errors=True)
            logger.debug(f"removed claude projects dir {entry}")


def install_signal_handlers(cleanup: Callable[[], None]) -> None:
    def handler(signum, _frame):
        logger.info(f"received signal {signum}; cleaning up")
        try:
            cleanup()
        finally:
            sys.exit(128 + signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)


# ─── install.sh driver ────────────────────────────────────────────────────────


def _run_install_sh(args: list[str], *, log_file: Path, label: str, check: bool = True) -> int:
    """Invoke platform-integrations/install.sh and tee output to the log.

    install.sh auto-detects the local platform-integrations/ tree (it lives
    next to it), so no env var or download is needed.
    """
    if not INSTALL_SH.is_file():
        raise FileNotFoundError(f"install.sh not found at {INSTALL_SH}")
    cmd = ["bash", str(INSTALL_SH), *args]
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as log:
        log.write(f"\n# === {label} ===\n# cmd: {cmd}\n")
        log.flush()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        log.write(f"exit={proc.returncode}\n")
        log.write(f"stdout:\n{proc.stdout}\n")
        log.write(f"stderr:\n{proc.stderr}\n")
    if check and proc.returncode != 0:
        raise RuntimeError(f"install.sh {' '.join(args)} failed (exit {proc.returncode}); see {log_file}")
    return proc.returncode


_INSTALL_EXTRA_ARGS = {"codex": [], "bob": ["--mode", "lite"]}


def install_one(platform: str, workspace: Path, log_file: Path) -> None:
    """Install a single platform's plugin into its own workspace.

    Claude isn't installed here — it's loaded per-invocation via
    `claude --plugin-dir <repo-tree>` (see run_claude). Codex and bob
    are installed project-local via install.sh, scoped to `workspace`.
    """
    if platform == "claude":
        return
    extra = _INSTALL_EXTRA_ARGS[platform]
    logger.info(f"running install.sh install --platform {platform} --dir {workspace}")
    _run_install_sh(
        ["install", "--platform", platform, *extra, "--dir", str(workspace)],
        log_file=log_file,
        label=f"install.sh install --platform {platform}",
    )


def _verify_claude(expected_dir: Path) -> tuple[bool, str]:
    """Use `claude plugin list --json` to confirm the session-scoped install
    points at `expected_dir`.

    Passing `--plugin-dir <X>` registers a session-scoped plugin entry with
    `scope: "session"` and `installPath: <X>`; the listing surfaces it
    alongside the user's globally installed plugins, which is exactly the
    signal we want — proof from the host CLI that *this* invocation will
    load from <X>, regardless of what's globally installed.
    """
    try:
        proc = subprocess.run(
            ["claude", "--plugin-dir", str(expected_dir), "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"claude plugin list failed: {exc!r}"
    if proc.returncode != 0:
        return False, f"`claude plugin list` exit={proc.returncode}: {proc.stderr.strip()}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return False, f"unparseable claude plugin list output: {exc}"

    session_entries = [e for e in data if e.get("scope") == "session" and "evolve-lite" in (e.get("id") or "")]
    expected_str = str(expected_dir)
    for entry in session_entries:
        if entry.get("installPath") == expected_str:
            return True, f"claude plugin list reports session install at {expected_str}"
    if session_entries:
        return False, (f"claude session install at {session_entries[0].get('installPath')!r}, expected {expected_str!r}")
    return False, f"no session-scoped evolve-lite in `claude --plugin-dir {expected_str} plugin list`"


def _verify_codex(workspace: Path) -> tuple[bool, str]:
    """Pre-flight checks for codex.

    Two layers, both load-bearing:

    1. **Marketplace.json points at the workspace plugin tree.** Necessary
       but not sufficient — this used to be the *only* check, which gave a
       false ✓ when codex actually loaded a stale cached plugin instead of
       the workspace one (see CODEX_HOME comment in setup_isolated_codex_home).

    2. **Isolated CODEX_HOME is set up with auth.** Proves
       setup_isolated_codex_home() ran. Without this, codex would either
       cache to the user's real ~/.codex/ (defeats isolation) or fail to
       authenticate (no auth.json copy).

    A separate post-skill check (verify_codex_cache_matches_workspace)
    confirms codex actually loaded from CODEX_HOME after invocation —
    that's where the structural proof lives.
    """
    expected_plugin = (workspace / "plugins/evolve-lite").resolve()
    marketplace_json = workspace / ".agents/plugins/marketplace.json"
    if not marketplace_json.is_file():
        return False, f"codex marketplace.json missing at {marketplace_json}"
    try:
        data = json.loads(marketplace_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"codex marketplace.json malformed: {exc}"
    entries = [p for p in data.get("plugins", []) if p.get("name") == "evolve-lite"]
    if not entries:
        return False, f"no evolve-lite entry in {marketplace_json}"
    rel = (entries[0].get("source") or {}).get("path") or ""
    # Codex marketplace.json paths are workspace-relative (project root),
    # not relative to the marketplace.json file's own directory — that's
    # how install.sh writes them (`./plugins/evolve-lite`) and how codex
    # resolves them at discovery time.
    resolved = (workspace / rel).resolve() if rel else None
    if resolved != expected_plugin:
        return False, f"codex marketplace.json source resolves to {resolved}, expected {expected_plugin}"
    if not (expected_plugin / "skills").is_dir():
        return False, f"codex plugin tree missing under {expected_plugin}"

    codex_home = workspace / ".codex-home"
    if not codex_home.is_dir():
        return False, f"isolated CODEX_HOME not set up at {codex_home}"
    if not (codex_home / "auth.json").is_file():
        return False, (
            f"auth.json missing in {codex_home}; codex would fail to authenticate. Make sure ~/.codex/auth.json exists on the host."
        )
    return True, f"codex marketplace.json points at {expected_plugin}; isolated CODEX_HOME at {codex_home}"


def verify_codex_cache_matches_workspace(workspace: Path) -> tuple[bool, str]:
    """Post-skill check: codex's plugin cache contains *this* workspace's
    plugin source, not a stale or divergent version.

    setup_isolated_codex_home() populates
    <workspace>/.codex-home/plugins/cache/evolve-local/evolve-lite/<version>/
    with a flattened skills layout (skills/<skill-name>/SKILL.md, NOT
    skills/evolve-lite/<skill-name>/SKILL.md — the latter is what the
    source tree has, but codex's plugin loader walks the cache without
    honoring plugin.json's `skills` path, so we flatten on copy).

    This check confirms:
      * the cache directory exists with at least one version subdir,
      * cached recall SKILL.md matches the workspace's source recall
        SKILL.md byte-for-byte (proves we copied from the right place
        and that nothing overwrote it mid-run).
    """
    cache_root = workspace / ".codex-home" / "plugins" / "cache" / "evolve-local" / "evolve-lite"
    if not cache_root.is_dir():
        return False, f"codex plugin cache missing at {cache_root}"
    versions = sorted(v for v in cache_root.iterdir() if v.is_dir())
    if not versions:
        return False, f"cache root exists but no version subdir at {cache_root}"
    if len(versions) > 1:
        logger.warning(f"multiple codex cache versions: {[v.name for v in versions]}; comparing newest")
    cached = versions[-1]
    cached_skill = cached / "skills" / "recall" / "SKILL.md"
    workspace_skill = workspace / "plugins/evolve-lite/skills/evolve-lite/recall/SKILL.md"
    if not workspace_skill.is_file():
        return False, f"workspace recall SKILL.md missing at {workspace_skill}"
    if not cached_skill.is_file():
        return False, f"cached recall SKILL.md missing at {cached_skill}"
    if cached_skill.read_text(encoding="utf-8") != workspace_skill.read_text(encoding="utf-8"):
        return False, (f"cached SKILL.md content != workspace SKILL.md ({cached_skill} vs {workspace_skill}); cache was overwritten")
    return True, f"codex cache content matches workspace plugin ({cached})"


def _verify_bob(workspace: Path) -> tuple[bool, str]:
    """Bob has no CLI listing for project-local skills (`bob extensions list`
    only shows extensions under ~/.bob/extensions/, and these are skills
    under <workspace>/.bob/skills/). Per the brief, file presence in the
    workspace is enough: bob auto-discovers .bob/ from cwd, so the
    presence of skills at the expected path proves the load source."""
    skill = workspace / ".bob/skills/evolve-lite-learn/SKILL.md"
    if skill.is_file():
        return True, f"bob skill present at {skill}"
    return False, f"bob skill missing at {skill}"


def verify_install(platform: str, workspace: Path) -> tuple[bool, str]:
    """Pre-flight per-platform: confirm the host CLI will load skills from
    the path we installed/pointed it at. See _verify_<platform> docstrings.
    """
    if platform == "claude":
        return _verify_claude(CLAUDE_PLUGIN_DIR)
    if platform == "codex":
        return _verify_codex(workspace)
    if platform == "bob":
        return _verify_bob(workspace)
    raise AssertionError(platform)


# ─── plugin tree resolution (for direct subscribe.py invocation) ──────────────


def plugin_root_for(platform: str, workspace: Path) -> Path:
    """Where install.sh placed the plugin tree for `platform`.

    We need this to invoke subscribe.py directly (the smoke test wires its
    own bare-remote write-scope subscription rather than asking the LLM to
    do it), so the path has to match install.sh's output layout.
    """
    if platform == "claude":
        # install.sh's claude path doesn't unpack into the workspace; the
        # plugin lives under ~/.claude/plugins after `claude plugin install`.
        # Repo source has the same content, so use it for subscribe.py.
        return PLATFORM_INTEGRATIONS / "claude" / "plugins" / "evolve-lite"
    if platform == "codex":
        return workspace / "plugins" / "evolve-lite"
    if platform == "bob":
        return workspace / ".bob"
    raise AssertionError(platform)


# ─── workspace bootstrap ──────────────────────────────────────────────────────


def init_workspace(workspace: Path) -> None:
    """Create the workspace directory layout.

    We avoid `uv init` for two reasons: (1) it would pull network deps, and
    (2) the smoke test only cares that the host CLI loads the plugin and
    exercises the skills against `EVOLVE_DIR` — nothing in the skills cares
    about Python project metadata. So we just lay down a minimal repo.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "pyproject.toml").write_text(
        '[project]\nname = "evolve-smoke-workspace"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    (workspace / "README.md").write_text("# evolve-smoke-workspace\n", encoding="utf-8")


def find_marker_in_trajectory(evolve_dir: Path, marker: str) -> tuple[bool, Path | None]:
    """Look for `marker` in the most recent saved trajectory.

    Used for claude's recall check: claude's on_stop hook fires
    /evolve-lite:learn after every `claude -p` invocation, and the parent
    agent's post-learn response clobbers stdout — so the recall response
    that contains the seeded marker is no longer in captured stdout. The
    save-trajectory Stop hook (running before learn's Stop hook) writes
    the full transcript to ${EVOLVE_DIR}/trajectories/, which preserves
    the parent's recall response (and the forked recall sub-agent's
    tool_result, which is where the verbatim entity quote actually
    appears thanks to the forked_context branch in recall/SKILL.md.j2).
    Grepping that file lets us verify recall succeeded even when the
    final stdout is about a different skill.
    """
    traj_dir = evolve_dir / "trajectories"
    if not traj_dir.is_dir():
        return False, None
    candidates = list(traj_dir.glob("*.jsonl")) + list(traj_dir.glob("*.json"))
    if not candidates:
        return False, None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        text = latest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, None
    return (marker in text), latest


def entity_count(evolve_dir: Path) -> int:
    """Count entity .md files under ${EVOLVE_DIR}/entities/<type>/.

    Excludes the `subscribed/` subtree, which is the cloned write-scope repo
    (its own files come from publish, not learn) and would muddy the count.
    """
    entities_dir = evolve_dir / "entities"
    if not entities_dir.is_dir():
        return 0
    total = 0
    for sub in entities_dir.iterdir():
        if not sub.is_dir() or sub.name == "subscribed":
            continue
        total += sum(1 for _ in sub.rglob("*.md"))
    return total


SEED_PROMPT = (
    "Remember that this project is managed by uv. "
    "Add the `requests` package as a dependency, then write a one-line "
    "Python script that imports requests and prints its version. "
    "Run the script and report the version it printed."
)


def run_claude_seed(workspace: Path, evolve_dir: Path, log_file: Path) -> int:
    """Run a claude -p task that organically produces a tool-failure cycle.

    Why: learn's Step 2 looks for tool failures, retries, and corrections in
    the trajectory. Without a meaningful seed, learn correctly emits zero
    entities — which masks a broken extractor as a passing test.

    The prompt: a uv-managed-project constraint plus a 'add requests, run a
    script' task. The model typically reaches for `pip install` or
    `python3 -c 'import requests'` first, hits ModuleNotFoundError or a
    pip-vs-uv mismatch, and recovers via `uv add requests` + `uv run`. The
    'Remember' framing is intentional — even though it can engage claude's
    auto-memory feature, anything written lives under the tempdir-scoped
    ~/.claude/projects/<encoded-tempdir>/memory/ that cleanup_claude_projects
    deletes at the end of the run.

    Side-effect chain (claude only):
      1. seed session runs the task and exits.
      2. save-trajectory Stop hook copies the transcript to
         ${EVOLVE_DIR}/trajectories/claude-transcript_<id>.jsonl.
      3. learn Stop hook blocks the agent, claude re-engages with
         /evolve-lite:learn, the forked sub-agent reads the trajectory and
         saves entities to ${EVOLVE_DIR}/entities/.
    """
    rc, _ = run_claude(SEED_PROMPT, cwd=workspace, evolve_dir=evolve_dir, log_file=log_file, label="seed")
    return rc


def seed_recall_entity(evolve_dir: Path, marker: str) -> Path:
    guideline_dir = evolve_dir / "entities" / "guideline"
    guideline_dir.mkdir(parents=True, exist_ok=True)
    path = guideline_dir / "smoke-recall-seed.md"
    path.write_text(
        textwrap.dedent(
            f"""\
            ---
            type: guideline
            trigger: When running the evolve-lite smoke test
            ---

            {marker} — this is the seeded recall entity. If you can read this,
            recall is wired correctly end-to-end.

            ## Rationale

            Smoke-test marker for the recall skill.
            """
        ),
        encoding="utf-8",
    )
    return path


def seed_publish_entity(evolve_dir: Path, name: str) -> Path:
    guideline_dir = evolve_dir / "entities" / "guideline"
    guideline_dir.mkdir(parents=True, exist_ok=True)
    path = guideline_dir / name
    path.write_text(
        textwrap.dedent(
            """\
            ---
            type: guideline
            trigger: When the smoke test exercises publish
            ---

            Smoke-test guideline destined for the bare git remote.

            ## Rationale

            If this lands as a commit on the bare remote, publish works end-to-end.
            """
        ),
        encoding="utf-8",
    )
    return path


# ─── bare git remote (publish target) ─────────────────────────────────────────


def init_bare_remote(remote_path: Path) -> None:
    """Create a bare repo with a single empty commit on `main` so subscribe
    can clone it without `error: Remote branch main not found`."""
    remote_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote_path)],
        check=True,
        capture_output=True,
    )
    seed_dir = remote_path.parent / f"_seed_{remote_path.name}"
    seed_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Force a stable identity so `git commit` doesn't error in CI-like envs.
    env.setdefault("GIT_AUTHOR_NAME", "Smoke Bot")
    env.setdefault("GIT_AUTHOR_EMAIL", "smoke@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "Smoke Bot")
    env.setdefault("GIT_COMMITTER_EMAIL", "smoke@example.invalid")
    for cmd in (
        ["git", "init", "-b", "main"],
        ["git", "commit", "--allow-empty", "-m", "init"],
        ["git", "remote", "add", "origin", str(remote_path)],
        ["git", "push", "origin", "main"],
    ):
        subprocess.run(cmd, cwd=seed_dir, check=True, capture_output=True, env=env)
    shutil.rmtree(seed_dir, ignore_errors=True)


def remote_commit_count(remote_path: Path) -> int:
    """Number of commits reachable from `main` on the bare remote."""
    result = subprocess.run(
        ["git", "--git-dir", str(remote_path), "rev-list", "--count", "main"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return -1
    return int((result.stdout or "0").strip() or 0)


def remote_log(remote_path: Path) -> str:
    result = subprocess.run(
        ["git", "--git-dir", str(remote_path), "log", "--oneline", "main"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (result.stdout or result.stderr or "").strip()


# ─── subscribe shortcut (bypasses LLM for repo wiring) ────────────────────────


def subscribe_write_repo(plugin_root: Path, evolve_dir: Path, remote: Path, repo_name: str) -> None:
    """Run subscribe.py directly so the publish step has a configured target.

    The smoke goal is to exercise publish end-to-end via the LLM, but we don't
    need the LLM to set up subscriptions — that's a config-mechanic step the
    user typically does once. Driving subscribe.py directly keeps the smoke
    test focused on what's actually load-bearing: publish writes to the
    cloned write-scope repo, then the agent commits and pushes.
    """
    # Locate the subscribe script. claude/codex use skills/evolve-lite/subscribe;
    # bob uses skills/evolve-lite-subscribe.
    candidates = [
        plugin_root / "skills" / "evolve-lite" / "subscribe" / "scripts" / "subscribe.py",
        plugin_root / "skills" / "evolve-lite-subscribe" / "scripts" / "subscribe.py",
    ]
    subscribe_py = next((c for c in candidates if c.is_file()), None)
    if subscribe_py is None:
        raise FileNotFoundError(f"subscribe.py not found under {plugin_root}")

    env = os.environ.copy()
    env["EVOLVE_DIR"] = str(evolve_dir)
    subprocess.run(
        [
            sys.executable,
            str(subscribe_py),
            "--name",
            repo_name,
            "--remote",
            str(remote),
            "--scope",
            "write",
            "--branch",
            "main",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def write_identity(project_root: Path, user: str = "smoke-bot") -> None:
    """publish.py reads identity.user from evolve.config.yaml and stamps it
    on the published entity. subscribe.py wrote the `repos:` section already;
    we only need to add `identity:` to the same file.

    `project_root` is the directory holding evolve.config.yaml. With
    EVOLVE_DIR=<workspace>/.evolve, subscribe.py treats <workspace> as
    the project root (the `.evolve` name triggers parent-as-root logic
    in subscribe.py:67), so pass the workspace here.
    """
    cfg_path = project_root / "evolve.config.yaml"
    existing = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    if "identity:" not in existing:
        cfg_path.write_text(f"identity:\n  user: {user}\n{existing}", encoding="utf-8")


# ─── command runners (one per host) ───────────────────────────────────────────


def _bytes_or_str_to_str(x: str | bytes | None) -> str:
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="replace")
    return x


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_file: Path,
    label: str,
) -> tuple[int, str]:
    """Run a host CLI command, tee output to a log, return (exit_code, output)."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as log:
        log.write(f"\n# === {label} ===\n# cmd: {cmd}\n# cwd: {cwd}\n")
        log.flush()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=PER_INVOCATION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess sets text=True so exc.stdout/stderr are str at
            # runtime, but the typeshed signature is `str | bytes | None`
            # (bytes when text/universal_newlines is unset). Coerce defensively
            # so mypy is happy and a future caller flipping text=False doesn't
            # silently print `b'...'`.
            so_out = _bytes_or_str_to_str(exc.stdout)
            so_err = _bytes_or_str_to_str(exc.stderr)
            log.write(f"TIMEOUT after {PER_INVOCATION_TIMEOUT_SECONDS}s\n")
            log.write(f"stdout-so-far:\n{so_out}\n")
            log.write(f"stderr-so-far:\n{so_err}\n")
            return 124, so_out + so_err
        log.write(f"exit={proc.returncode}\n")
        log.write(f"stdout:\n{proc.stdout}\n")
        log.write(f"stderr:\n{proc.stderr}\n")
        return proc.returncode, proc.stdout + "\n" + proc.stderr


def run_claude(prompt: str, *, cwd: Path, evolve_dir: Path, log_file: Path, label: str) -> tuple[int, str]:
    env = os.environ.copy()
    env["EVOLVE_DIR"] = str(evolve_dir)
    # --plugin-dir points claude at this repo's rendered claude plugin tree
    # for the duration of this invocation, sidestepping the user's real
    # ~/.claude/ install. This is install.sh's documented manual fallback
    # (see platform-integrations/INSTALL_SPEC.md).
    #
    # We deliberately do NOT pass --no-session-persistence: that flag stops
    # claude from writing the transcript to ~/.claude/projects/, which
    # leaves save-trajectory's Stop hook with no `transcript_path` file to
    # copy into ${EVOLVE_DIR}/trajectories/ — so learn would have nothing
    # to extract from. Persistence pollutes ~/.claude/projects/ with one
    # `<encoded-tempdir>/` subtree per smoke run, which we explicitly clean
    # up in `cleanup_claude_projects()`.
    cmd = [
        "claude",
        "--plugin-dir",
        str(CLAUDE_PLUGIN_DIR),
        "--dangerously-skip-permissions",
        "-p",
        prompt,
    ]
    return _run(cmd, cwd=cwd, env=env, log_file=log_file, label=label)


def run_codex(prompt: str, *, cwd: Path, evolve_dir: Path, log_file: Path, label: str) -> tuple[int, str]:
    env = os.environ.copy()
    env["EVOLVE_DIR"] = str(evolve_dir)
    # CODEX_HOME points at the workspace-local codex home set up by
    # setup_isolated_codex_home(); ensures codex's plugin cache lives
    # under the tempdir and gets wiped with it. setup_isolated_codex_home
    # also writes a fresh config.toml with the marketplace+plugin entries
    # codex needs to register `$evolve-lite:<skill>` into its skill
    # registry — see that function's docstring for why all three steps
    # are required. We do NOT pass --ignore-user-config: from codex's
    # perspective $CODEX_HOME/config.toml IS the user config, and
    # ignoring it would skip the persisted [plugins."evolve-lite@..."]
    # entry that triggers plugin discovery at startup.
    env["CODEX_HOME"] = str(cwd / ".codex-home")
    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "-c",
        "features.codex_hooks=true",
        "-C",
        str(cwd),
        prompt,
    ]
    return _run(cmd, cwd=cwd, env=env, log_file=log_file, label=label)


def run_bob(prompt: str, *, cwd: Path, evolve_dir: Path, log_file: Path, label: str) -> tuple[int, str]:
    env = os.environ.copy()
    env["EVOLVE_DIR"] = str(evolve_dir)
    cmd = [
        "bob",
        "--yolo",
        "--hide-intermediary-output",
        prompt,
    ]
    return _run(cmd, cwd=cwd, env=env, log_file=log_file, label=label)


# ─── per-platform driver ──────────────────────────────────────────────────────


@dataclass
class PlatformPlan:
    name: str
    cli: str  # binary on PATH
    learn_cmd: str  # slash command text to send for learn
    publish_cmd: str  # slash command text to invoke publish
    recall_prompt: str  # full prompt for recall


def claude_plan() -> PlatformPlan:
    return PlatformPlan(
        name="claude",
        cli="claude",
        learn_cmd="/evolve-lite:learn",
        publish_cmd="/evolve-lite:publish",
        recall_prompt=(
            "Use /evolve-lite:recall on this conversation. After running it, "
            "quote any retrieved entity content verbatim — do not paraphrase. "
            "If nothing is retrieved, say 'NO ENTITIES FOUND'."
        ),
    )


def codex_plan() -> PlatformPlan:
    # All codex invocations use `$<skill>` — that's the registry-lookup
    # form (see openai/codex#11817). The whole point of this smoke test
    # is to verify the plugin's skills are actually installed and
    # discoverable by codex's runtime, NOT just that SKILL.md exists on
    # disk for the model to fall back on. If a `$<skill>` invocation
    # fails because codex says "skill not in available list", that's a
    # real install/registration bug, not a prompt issue.
    return PlatformPlan(
        name="codex",
        cli="codex",
        learn_cmd="$evolve-lite:learn",
        publish_cmd="$evolve-lite:publish",
        recall_prompt=(
            "Use $evolve-lite:recall on this conversation. After running it, "
            "quote any retrieved entity content verbatim — do not paraphrase. "
            "If nothing is retrieved, say 'NO ENTITIES FOUND'."
        ),
    )


def bob_plan() -> PlatformPlan:
    # Bob's commands are flat-named: /evolve-lite-recall, /evolve-lite-learn, etc.
    return PlatformPlan(
        name="bob",
        cli="bob",
        learn_cmd="/evolve-lite-learn",
        publish_cmd="/evolve-lite-publish",
        recall_prompt=(
            "Run /evolve-lite-recall on this conversation. After running it, "
            "quote any retrieved entity content verbatim — do not paraphrase. "
            "If nothing is retrieved, say 'NO ENTITIES FOUND'."
        ),
    )


def cli_present(name: str) -> bool:
    return shutil.which(name) is not None


def run_platform(platform: str, root_tempdir: Path) -> PlatformResult:
    """Self-contained per-platform run: setup + skill flow.

    Each platform owns its own subdir under root_tempdir, so multiple
    `run_platform` calls can execute concurrently without sharing
    workspace, remotes, or log files. The thread name (set by main's
    ThreadPoolExecutor wrapper) becomes the logging prefix.
    """
    threading.current_thread().name = platform
    result = PlatformResult(platform=platform)

    plan = {"claude": claude_plan(), "codex": codex_plan(), "bob": bob_plan()}[platform]

    if not cli_present(plan.cli):
        result.skipped_reason = f"`{plan.cli}` not found on PATH"
        logger.warning(result.skipped_reason)
        return result

    # Per-platform tempdir: <root>/<platform>/{workspace, remote.git, smoke.log}
    pdir = root_tempdir / platform
    workspace = pdir / "workspace"
    log_file = pdir / "smoke.log"
    result.log_path = log_file

    # ── install + verify (per-platform; concurrent-safe since each thread
    # has its own workspace).
    try:
        init_workspace(workspace)
        if platform == "codex":
            # Must come before any codex invocation. Sets up an isolated
            # plugin cache under <workspace>/.codex-home/ so the user's
            # global ~/.codex/plugins/cache/ is never read or written.
            setup_isolated_codex_home(workspace)
        install_one(platform, workspace, log_file)
        if platform == "codex":
            # Replicates the interactive `/plugin install` flow: registers
            # the marketplace, populates the plugin cache with a flattened
            # skills layout, and persists [plugins."x@y"].enabled=true in
            # CODEX_HOME's config.toml. Without this, the workspace's
            # marketplace.json is just metadata — codex's $-registry never
            # actually picks up the plugin's skills.
            register_codex_plugin(workspace)
    except Exception as exc:
        result.setup_error = f"install failed: {exc!r}"
        logger.warning(result.setup_error)
        return result

    ok, detail = verify_install(platform, workspace)
    if ok:
        logger.info(f"install ✓ {detail}")
    else:
        result.setup_error = f"install verify failed: {detail}"
        logger.warning(result.setup_error)
        return result

    # ── bob: skill execution disabled.
    # The end-to-end skill flow (seed → save-trajectory → learn → recall →
    # publish) requires multi-message session continuation so each slash
    # command lands at the head of a fresh user message — bob's parser
    # ignores slash commands embedded in mid-response assistant text.
    # `bob --resume latest` is the documented way to drive that flow but is
    # currently broken upstream, leaving us no way to exercise the skills
    # against bob without flaky one-shot prompt heuristics. Until --resume
    # is fixed we report bob as "install verified only" and skip the skill
    # invocations entirely; the install path is the only thing this smoke
    # can honestly verify on bob right now.
    if platform == "bob":
        msg = (
            "bob skill execution disabled: bob --resume is broken upstream "
            "and slash commands embedded in mid-response text aren't parsed, "
            "so the seed → save-trajectory → learn chain can't be driven "
            "reliably. Smoke verifies install only for this platform."
        )
        logger.warning(msg)
        result.skills.append(SkillResult(name="install-only", ok=True, detail=msg))
        return result

    # Use the canonical `.evolve/` name so the codex skill's SKILL.md
    # path defaults (`${EVOLVE_DIR:-.evolve}` interpreted by the model as
    # the literal `.evolve`) point at the right entities directory.
    # subscribe.py treats `evolve_dir.name == ".evolve"` as "I'm the
    # .evolve subdir of project_root=parent", which means the workspace
    # itself is the project root (where evolve.config.yaml lives) — see
    # write_identity(workspace, ...) below. Each platform runs in its
    # own workspace subdir, so the unsuffixed name doesn't collide.
    evolve_dir = workspace / ".evolve"
    evolve_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"workspace: {workspace}")
    logger.info(f"evolve_dir: {evolve_dir}")
    logger.info(f"log: {log_file}")

    plugin_root = plugin_root_for(platform, workspace)

    # ── bare remote + subscribe (publish target wiring)
    try:
        remote_path = pdir / "remote.git"
        init_bare_remote(remote_path)
        subscribe_write_repo(plugin_root, evolve_dir, remote_path, "smoke-target")
        # subscribe.py treats EVOLVE_DIR=<x>/.evolve as "<x> is project root",
        # so evolve.config.yaml lives at the workspace level, not inside .evolve/.
        write_identity(workspace)
        baseline_commits = remote_commit_count(remote_path)
        logger.debug(f"baseline commit count on {remote_path.name}: {baseline_commits}")
    except Exception as exc:
        result.setup_error = f"setup failed: {exc!r}"
        logger.warning(result.setup_error)
        return result

    # ── invocation helper bound to this platform's runner
    def invoke(prompt: str, label: str) -> tuple[int, str]:
        if platform == "claude":
            return run_claude(prompt, cwd=workspace, evolve_dir=evolve_dir, log_file=log_file, label=label)
        if platform == "codex":
            return run_codex(prompt, cwd=workspace, evolve_dir=evolve_dir, log_file=log_file, label=label)
        if platform == "bob":
            return run_bob(prompt, cwd=workspace, evolve_dir=evolve_dir, log_file=log_file, label=label)
        raise AssertionError(platform)

    # ── learn
    # All three platforms: real seed task, then verify entity count grew.
    # The chain differs by platform — see the module docstring for why:
    #   * claude: seed task alone; Stop hooks auto-fire save-trajectory + learn,
    #     and we do an extra explicit /evolve-lite:learn pass afterwards.
    #   * codex/bob: no Stop hooks for this. Suffix the seed prompt with the
    #     learn slash command so the same session invokes learn at the end
    #     (learn is main-context on those platforms — build_plugins.py only
    #     sets forked_context=True for claude — so it reads the conversation
    #     directly, no trajectory file needed).
    baseline_entities = entity_count(evolve_dir)
    if platform == "claude":
        logger.info("→ seed (real trajectory for learn)")
        t0 = time.time()
        seed_rc = run_claude_seed(workspace, evolve_dir, log_file)
        dt_seed = time.time() - t0
        post_seed = entity_count(evolve_dir)
        logger.debug(f"seed: exit={seed_rc} in {dt_seed:.1f}s; entities {baseline_entities}→{post_seed}")
        if seed_rc != 0:
            logger.warning(f"seed exited {seed_rc}; learn may have nothing to extract")

        logger.info("→ learn")
        t0 = time.time()
        rc, _ = invoke(plan.learn_cmd, "learn")
        dt = time.time() - t0
    else:
        logger.info("→ seed-and-learn")
        seed_and_learn_prompt = (
            f"{SEED_PROMPT}\n\n"
            f"After completing (or attempting) the task above, your final "
            f"action MUST be to run {plan.learn_cmd} so it can extract "
            f"learnings from this conversation."
        )
        t0 = time.time()
        rc, _ = invoke(seed_and_learn_prompt, "seed-and-learn")
        dt = time.time() - t0

    post_learn = entity_count(evolve_dir)
    ok = (rc == 0) and (post_learn > baseline_entities)
    if not ok and rc == 0:
        detail = f"exit=0 in {dt:.1f}s but entities still {post_learn} (baseline {baseline_entities}); learn extracted nothing"
    else:
        detail = f"exit={rc} in {dt:.1f}s; entities {baseline_entities}→{post_learn}"
    result.skills.append(SkillResult(name="learn", ok=ok, detail=detail))

    # ── recall (seed entity, prompt agent to echo it)
    logger.info("→ recall")
    marker = f"MARKER_{uuid.uuid4().hex[:12]}"
    seed_recall_entity(evolve_dir, marker)
    t0 = time.time()
    rc, output = invoke(plan.recall_prompt, "recall")
    dt = time.time() - t0
    if rc != 0:
        result.skills.append(SkillResult(name="recall", ok=False, detail=f"exit={rc} in {dt:.1f}s"))
    elif marker in output:
        result.skills.append(SkillResult(name="recall", ok=True, detail=f"marker echoed in {dt:.1f}s"))
    else:
        # Stdout fallback for claude: the on_stop learn hook fires after
        # every `claude -p` invocation and its post-learn response
        # clobbers stdout. The parent's actual recall response (with the
        # forked sub-agent's verbatim entity quote) is preserved in the
        # saved trajectory file, which we can grep for the marker.
        in_traj = False
        traj: Path | None = None
        if platform == "claude":
            in_traj, traj = find_marker_in_trajectory(evolve_dir, marker)
        if in_traj and traj is not None:
            result.skills.append(
                SkillResult(
                    name="recall",
                    ok=True,
                    detail=f"marker found in trajectory ({traj.name}) in {dt:.1f}s (stdout clobbered by on_stop)",
                )
            )
        else:
            result.skills.append(
                SkillResult(
                    name="recall",
                    ok=False,
                    detail=f"exit=0 in {dt:.1f}s but marker {marker!r} absent from output and trajectory (see log)",
                )
            )

    # ── publish (seed guideline, drive the slash command, verify bare remote)
    logger.info("→ publish")
    publish_filename = f"smoke-publish-{uuid.uuid4().hex[:8]}.md"
    seed_publish_entity(evolve_dir, publish_filename)
    publish_prompt = (
        f"Run the publish skill ({plan.publish_cmd}). "
        f"Publish exactly the file `{publish_filename}` from the configured EVOLVE_DIR "
        f"({evolve_dir})/entities/guideline/ to the configured write-scope repo `smoke-target`. "
        f"The user is `smoke-bot`. After the publish.py script succeeds, "
        f"`git -C {evolve_dir}/entities/subscribed/smoke-target add guideline/{publish_filename} && "
        f"git -C {evolve_dir}/entities/subscribed/smoke-target commit -m '[evolve] publish: {publish_filename}' && "
        f"git -C {evolve_dir}/entities/subscribed/smoke-target push origin main`. "
        f"Do not ask for confirmation; proceed end-to-end."
    )
    t0 = time.time()
    rc, _ = invoke(publish_prompt, "publish")
    dt = time.time() - t0
    after_commits = remote_commit_count(remote_path)
    if rc != 0:
        result.skills.append(SkillResult(name="publish", ok=False, detail=f"exit={rc} in {dt:.1f}s"))
    elif after_commits > baseline_commits:
        result.skills.append(
            SkillResult(
                name="publish",
                ok=True,
                detail=f"bare remote went {baseline_commits}→{after_commits} commits in {dt:.1f}s",
            )
        )
    else:
        result.skills.append(
            SkillResult(
                name="publish",
                ok=False,
                detail=(
                    f"exit=0 in {dt:.1f}s but no new commit on bare remote "
                    f"(still {after_commits} commits). Last log:\n{remote_log(remote_path)}"
                ),
            )
        )

    # ── cache integrity (codex only): codex's plugin cache must mirror
    # the workspace plugin tree. If it doesn't, the smoke ran against the
    # wrong source and any pass results above are suspect.
    if platform == "codex":
        ok, detail = verify_codex_cache_matches_workspace(workspace)
        result.skills.append(SkillResult(name="cache-integrity", ok=ok, detail=detail))

    return result


# ─── orchestrator ─────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--platform",
        choices=(*PLATFORMS, "all"),
        default="all",
        help="Which platform(s) to test (default: all)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Don't delete the tempdir on exit (for debugging)",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Run platforms one at a time instead of in parallel (for debugging interleaving issues).",
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="Force line-prefix output even on a TTY (default: live grouped view in a TTY when running multi-platform).",
    )
    parser.add_argument("--verbose", action="store_true", help="Chatty output")
    args = parser.parse_args(argv)

    targets = PLATFORMS if args.platform == "all" else (args.platform,)

    # Live-grouped TTY view only makes sense for parallel multi-platform runs
    # in an interactive terminal. Anywhere output is captured (pipe, file,
    # agent harness) we drop down to line-prefix mode so the captured stream
    # is greppable plain text without ANSI escapes.
    live_view = sys.stdout.isatty() and not args.no_live and not args.sequential and len(targets) > 1
    log_handler = setup_logging(
        args.verbose,
        live=live_view,
        group_order=("MainThread", *targets),
    )

    logger.info(f"targets: {', '.join(targets)}")

    tempdir = make_root_tempdir()
    cleanup_done = {"flag": False}

    def cleanup() -> None:
        # Idempotent — both the signal handler and the finally clause may call us.
        # Each platform owns a workspace under tempdir/<platform>/workspace; we
        # also remove claude's per-cwd ~/.claude/projects/<encoded-cwd>/ entries
        # for any workspace that may have triggered persistence.
        if cleanup_done["flag"]:
            return
        cleanup_done["flag"] = True
        for plat in targets:
            cleanup_claude_projects(tempdir / plat / "workspace")
        if args.keep:
            logger.info(f"--keep set; leaving {tempdir}")
            return
        shutil.rmtree(tempdir, ignore_errors=True)
        logger.info(f"removed {tempdir}")

    install_signal_handlers(cleanup)

    results: list[PlatformResult] = []
    try:
        if args.sequential or len(targets) == 1:
            for platform in targets:
                results.append(_run_platform_safely(platform, tempdir))
        else:
            # Concurrent: each platform runs in its own thread with its own
            # workspace, log file, and bare remote. ThreadPoolExecutor's worker
            # threads inherit the root logger config, so logs from each thread
            # land on the shared StreamHandler atomically (one record per write
            # call) — the threadName field in the format string identifies the
            # source platform without any manual locking.
            with ThreadPoolExecutor(max_workers=len(targets), thread_name_prefix="smoke") as ex:
                futures = {ex.submit(_run_platform_safely, p, tempdir): p for p in targets}
                for fut in as_completed(futures):
                    results.append(fut.result())
            # Re-sort to match input order so the summary reads top-to-bottom.
            order = {p: i for i, p in enumerate(targets)}
            results.sort(key=lambda r: order.get(r.platform, len(targets)))
    finally:
        cleanup()
        if isinstance(log_handler, LiveGroupedHandler):
            # End the redraw region so the summary prints below the final
            # state of the live view instead of getting overwritten.
            log_handler.finalize()

    return _print_summary(results)


def _run_platform_safely(platform: str, tempdir: Path) -> PlatformResult:
    """Wrapper that catches unexpected exceptions so one platform crashing
    can't prevent the others from completing or being summarized.
    """
    try:
        return run_platform(platform, tempdir)
    except Exception as exc:
        logger.warning(f"unhandled exception in {platform}: {exc!r}")
        return PlatformResult(platform=platform, setup_error=f"unhandled: {exc!r}")


def _print_summary(results: list[PlatformResult]) -> int:
    section("summary")
    any_failed = False
    for r in results:
        if r.skipped_reason:
            print(f"  {r.platform:7s}  SKIPPED   {r.skipped_reason}")
            any_failed = True
            continue
        if r.setup_error:
            print(f"  {r.platform:7s}  SETUP ✗   {r.setup_error}  (log: {r.log_path})")
            any_failed = True
            continue
        verdict = "PASS" if r.overall_ok else "FAIL"
        print(f"  {r.platform:7s}  {verdict}")
        for s in r.skills:
            mark = "✓" if s.ok else "✗"
            print(f"             {mark} {s.name:8s}  {s.detail}")
        if not r.overall_ok:
            any_failed = True
            if r.log_path:
                print(f"             log: {r.log_path}")
    print()
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
