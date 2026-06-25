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

This skill checks whether the import is actually reaching your sessions, by
looking for a canary token that the installed EVOLVE.md expands into the session
transcript when the import loads.

## Required Action

Run the doctor script from the repo root:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/evolve-lite/doctor/scripts/doctor.py
```

It is read-only and always exits 0. Read the status code it prints:

- **OK** — the import is loading; nothing to do.
- **IMPORT_DISABLED** — the `@import` line is in `CLAUDE.md` but its content is
  not reaching sessions (you likely declined the external-import approval).
  Follow the remediation the script prints: purge the project approval, start a
  new session, and **Allow** the import dialog.
- **NOT_INSTALLED** — evolve isn't wired into this repo; re-run the installer.
- **STALE_EVOLVE_MD** — the installed `.evolve/EVOLVE.md` predates the canary;
  re-run the installer to refresh it.
- **UNKNOWN** — no recent Claude transcripts for this project yet; open a
  session, then re-run.

Relay the status and any remediation to the user.

