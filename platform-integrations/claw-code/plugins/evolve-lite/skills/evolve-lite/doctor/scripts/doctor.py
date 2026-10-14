#!/usr/bin/env python3
"""
Doctor Script (Claude-only diagnostic)

On Claude, evolve is delivered by a single ``@.evolve/EVOLVE.md`` import line in
the repo's ``./CLAUDE.md``. That import requires a one-time, per-project
"allow external imports" approval. If the user declines it (even once, in a past
session) Claude silently disables the import forever — the thin EVOLVE.md never
loads and evolve becomes a no-op with NO error.

Claude's internal approval flag is undocumented and unreliable to read, so the
doctor detects delivery *empirically*: the installed thin EVOLVE.md carries a
visible canary line (``Evolve import canary: EVOLVE_IMPORT_CANARY_<v>``). Only
the model can see what reached its context — Claude does not write CLAUDE.md or
its imports into session transcripts, and strips HTML comments from them — so
the doctor skill checks its own context for that line and passes what it found
here. This script extracts the expected token from the installed copy (never
hardcoding it twice) and compares.

Status codes (printed verbatim, always exit 0 — this is a diagnostic):

    OK              — the session saw the installed canary; import is loading.
    IMPORT_DISABLED — import line present in CLAUDE.md but the session did not
                      see the canary; the user likely declined the
                      external-import approval.
    NOT_INSTALLED   — the import line is missing from CLAUDE.md, or the installed
                      .evolve/EVOLVE.md is missing; run the installer.
    STALE_EVOLVE_MD — installed EVOLVE.md has no visible canary line (it
                      predates this build), or the session saw a different
                      canary than the installed one; re-run the installer.
    UNKNOWN         — called without --seen/--not-seen, so there is nothing to
                      compare; run it through /evolve-lite:doctor.

Usage:
    python3 doctor.py --seen EVOLVE_IMPORT_CANARY_<v>
    python3 doctor.py --not-seen
"""

import argparse
import os
import re
import sys
from pathlib import Path

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
from entity_io import log as _log  # noqa: E402


def log(message):
    _log("doctor", message)


# The line the installer injects into the repo's CLAUDE.md (see install.sh
# CLAUDE_IMPORT_LINE). Matching on this substring is the install-sanity check.
CLAUDE_IMPORT_LINE = "@.evolve/EVOLVE.md"

# Pattern used to lift the canary token out of the installed EVOLVE.md so the
# exact token lives in exactly one place (the template), never duplicated here.
# Anchored to a visible line: older builds kept the token in an HTML comment,
# which Claude strips before the model sees it, so those count as stale.
_CANARY_RE = re.compile(r"^Evolve import canary: (EVOLVE_IMPORT_CANARY_\S+)", re.MULTILINE)


def _evolve_dir(root):
    """Resolve the .evolve root: $EVOLVE_DIR if set, else <root>/.evolve."""
    env_dir = os.environ.get("EVOLVE_DIR")
    if env_dir:
        return Path(env_dir)
    return root / ".evolve"


def diagnose(root, seen):
    """Core diagnosis. Returns ``(code, message)``; never raises on missing
    files/dirs. `root` is the project root; `seen` is the canary token the
    session found in its own context, ``False`` if it found none, or ``None``
    if the caller did not say.
    """
    root = Path(root)

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
            f"installed EVOLVE.md at {evolve_md} has no canary line (it predates this build); re-run the installer to refresh it.",
        )
    token = match.group(1)

    # --- Compare with what the session saw -----------------------------------
    if seen is None:
        return (
            "UNKNOWN",
            "no session result given; run this through /evolve-lite:doctor, which checks the session's own context for the canary.",
        )
    if seen == token:
        return ("OK", "✓ evolve EVOLVE.md import is loading.")
    if seen:
        return (
            "STALE_EVOLVE_MD",
            f"this session loaded canary {seen} but the installed EVOLVE.md carries {token}; "
            "re-run the installer, then start a new session.",
        )

    return (
        "IMPORT_DISABLED",
        "⚠ The @import is present in CLAUDE.md but its content is NOT "
        "reaching sessions — you likely declined Claude's external-import "
        "approval. Re-enable by running `claude project purge "
        f"{root}` then start a new session and Allow the import dialog.",
    )


def main():
    parser = argparse.ArgumentParser(description="Check that the evolve EVOLVE.md import reaches Claude sessions.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--seen", metavar="TOKEN", help="canary token the session found in its own context")
    group.add_argument("--not-seen", action="store_true", help="the session found no canary in its own context")
    args = parser.parse_args()
    seen = args.seen if args.seen is not None else (False if args.not_seen else None)

    root = Path(os.getcwd()).resolve()
    code, message = diagnose(root, seen)
    log(f"{code}: {message}")
    print(f"evolve doctor [{code}] {message}")
    # Diagnostic only — never fail the caller.
    sys.exit(0)


if __name__ == "__main__":
    main()
