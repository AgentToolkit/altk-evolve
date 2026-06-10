#!/usr/bin/env python3
"""
Doctor Script (Claude-only diagnostic)

On Claude, evolve is delivered by a single ``@.evolve/EVOLVE.md`` import line in
the repo's ``./CLAUDE.md``. That import requires a one-time, per-project
"allow external imports" approval. If the user declines it (even once, in a past
session) Claude silently disables the import forever — the thin EVOLVE.md never
loads and evolve becomes a no-op with NO error.

Claude's internal approval flag is undocumented and unreliable to read, so this
script detects delivery *empirically*: the installed thin EVOLVE.md carries a
canary token (``EVOLVE_IMPORT_CANARY_<v>``). When the import loads, that token
expands into the session transcript. The doctor extracts the token from the
installed copy (never hardcoding it twice) and greps the most recent Claude
project transcripts for it.

Status codes (printed verbatim, always exit 0 — this is a diagnostic):

    OK              — canary found in a recent transcript; import is loading.
    IMPORT_DISABLED — import line present in CLAUDE.md but canary absent from
                      every recent transcript; the user likely declined the
                      external-import approval.
    NOT_INSTALLED   — the import line is missing from CLAUDE.md, or the installed
                      .evolve/EVOLVE.md is missing; run the installer.
    STALE_EVOLVE_MD — installed EVOLVE.md has no canary; it predates this build,
                      re-run the installer.
    UNKNOWN         — no recent Claude transcripts for this project yet.

Usage:
    python3 doctor.py
"""

import os
import re
import sys
from pathlib import Path

# Note: the shared lib import below provides `claude_project_slug`, the single
# source of truth for the ~/.claude/projects/<slug>/ directory name (shared with
# adapt_memory.py).

# Walk up from the script location to find the installed plugin lib directory.
# Every host installs the shared lib under lib/evolve-lite/ so multiple plugins
# can coexist side by side. The doctor only needs the shared `log` helper, but
# resolving the lib the same way the other scripts do keeps the convention
# uniform (and only works in the rendered tree, same constraint as adapt_memory).
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    _candidate = _ancestor / "lib" / "evolve-lite"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
        break
if _lib is None:
    raise ImportError(f"Cannot find plugin lib directory above {_script}")
sys.path.insert(0, str(_lib))
from entity_io import claude_project_slug, log as _log  # noqa: E402


def log(message):
    _log("doctor", message)


# The line the installer injects into the repo's CLAUDE.md (see install.sh
# CLAUDE_IMPORT_LINE). Matching on this substring is the install-sanity check.
CLAUDE_IMPORT_LINE = "@.evolve/EVOLVE.md"

# Pattern used to lift the canary token out of the installed EVOLVE.md so the
# exact token lives in exactly one place (the template), never duplicated here.
_CANARY_RE = re.compile(r"EVOLVE_IMPORT_CANARY_\S+")

# How many of the most-recent transcripts to scan for the canary.
_RECENT_N = 3


def _evolve_dir(root):
    """Resolve the .evolve root: $EVOLVE_DIR if set, else <root>/.evolve."""
    env_dir = os.environ.get("EVOLVE_DIR")
    if env_dir:
        return Path(env_dir)
    return root / ".evolve"


def _recent_transcripts(home, root, limit=_RECENT_N):
    """The most recent N ``*.jsonl`` transcripts for this project, by mtime."""
    # Claude derives a project's transcript dir name the same way it derives the
    # native memory dir name — see entity_io.claude_project_slug (one source of
    # truth, shared with adapt_memory.py).
    slug = claude_project_slug(root)
    proj_dir = home / ".claude" / "projects" / slug
    if not proj_dir.is_dir():
        return []
    jsonl = [p for p in proj_dir.glob("*.jsonl") if p.is_file()]
    jsonl.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonl[:limit]


def _canary_in_transcripts(transcripts, token):
    """True if `token` appears anywhere in any of the given transcript files."""
    for path in transcripts:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if token in text:
            return True
    return False


def diagnose(root, home):
    """Core diagnosis. Returns ``(code, message)``; never raises on missing
    files/dirs. `root` is the project root; `home` is the user home dir under
    which Claude keeps ``~/.claude/projects/<slug>/``.
    """
    root = Path(root)
    home = Path(home)

    # --- Install sanity ------------------------------------------------------
    claude_md = root / "CLAUDE.md"
    has_import = False
    if claude_md.is_file():
        try:
            has_import = CLAUDE_IMPORT_LINE in claude_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            has_import = False
    if not has_import:
        return (
            "NOT_INSTALLED",
            f"evolve import not wired into this repo's CLAUDE.md (expected a line `{CLAUDE_IMPORT_LINE}`); run the installer.",
        )

    evolve_md = _evolve_dir(root) / "EVOLVE.md"
    if not evolve_md.is_file():
        return (
            "NOT_INSTALLED",
            f"installed EVOLVE.md is missing at {evolve_md}; run the installer.",
        )

    # --- Extract the canary from the installed file --------------------------
    try:
        evolve_text = evolve_md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return (
            "NOT_INSTALLED",
            f"cannot read installed EVOLVE.md at {evolve_md} - {exc}; run the installer.",
        )
    match = _CANARY_RE.search(evolve_text)
    if not match:
        return (
            "STALE_EVOLVE_MD",
            f"installed EVOLVE.md at {evolve_md} has no canary token (it predates this build); re-run the installer to refresh it.",
        )
    token = match.group(0)

    # --- Transcript check ----------------------------------------------------
    transcripts = _recent_transcripts(home, root)
    if not transcripts:
        return (
            "UNKNOWN",
            "no recent Claude transcripts for this project yet; open a session, then re-run.",
        )
    if _canary_in_transcripts(transcripts, token):
        return ("OK", "✓ evolve EVOLVE.md import is loading.")

    return (
        "IMPORT_DISABLED",
        "⚠ The @import is present in CLAUDE.md but its content is NOT "
        "reaching sessions — you likely declined Claude's external-import "
        "approval. Re-enable by running `claude project purge "
        f"{root}` then start a new session and Allow the import dialog.",
    )


def main():
    root = Path(os.getcwd()).resolve()
    home = Path.home()
    code, message = diagnose(root, home)
    log(f"{code}: {message}")
    print(f"evolve doctor [{code}] {message}")
    # Diagnostic only — never fail the caller.
    sys.exit(0)


if __name__ == "__main__":
    main()
