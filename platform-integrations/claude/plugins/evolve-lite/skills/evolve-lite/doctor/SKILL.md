---
name: doctor
description: Diagnose evolve health on Claude — verify the CLAUDE.md @import is actually loading the thin EVOLVE.md into sessions
context: fork
---

# Doctor

## Overview

On Claude, evolve is delivered by a single `@.evolve/EVOLVE.md` import line in
this repo's `./CLAUDE.md`. That import requires a one-time, per-project "allow
external imports" approval. If you (or a teammate) declined it — even once, in a
past session — Claude silently disables the import forever, the thin EVOLVE.md
never loads, and evolve becomes a no-op with **no error**.

This skill checks whether the import is actually reaching your sessions. The
installed EVOLVE.md carries a canary line, and only you can tell whether it
reached your context: Claude does not write CLAUDE.md or its imports into
session transcripts, so there is nothing on disk to check instead.

## Required Action

1. Look in the project instructions already loaded into your context (the
   `CLAUDE.md` contents and anything it imports) for a line that starts with
   `Evolve import canary:`. Do **not** read, open or search any file for it —
   reading `.evolve/EVOLVE.md` would find the line whether or not the import
   works, and defeat the check.

2. Run the doctor script from the repo root, passing what you found. If you
   found the line, pass the `EVOLVE_IMPORT_CANARY_…` token from it:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/doctor/scripts/doctor.py --seen <token>
   ```

   If you did not find it:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/doctor/scripts/doctor.py --not-seen
   ```

It is read-only and always exits 0. Read the status code it prints:

- **OK** — the import is loading; nothing to do.
- **IMPORT_DISABLED** — the `@import` line is in `CLAUDE.md` but its content is
  not reaching sessions (you likely declined the external-import approval).
  Follow the remediation the script prints: purge the project approval, start a
  new session, and **Allow** the import dialog.
- **NOT_INSTALLED** — evolve isn't wired into this repo; re-run the installer.
- **STALE_EVOLVE_MD** — the installed `.evolve/EVOLVE.md` predates the current
  canary, or this session loaded an older copy; re-run the installer to refresh
  it, then start a new session.
- **UNKNOWN** — the script was run without `--seen` or `--not-seen`; follow the
  steps above.

Relay the status and any remediation to the user.

