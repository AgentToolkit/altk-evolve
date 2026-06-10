---
name: adapt-memory
description: Mirror a just-saved native memory into the shared evolve store so it becomes shareable and auditable
context: fork
---

# Adapt Memory

## Overview

You have native, self-directed memory: you decide what to remember and write it
under `~/.claude/projects/<hash>/memory/`. That store has no provenance or
sharing. This skill mirrors a memory you just saved into the in-repo evolve
store at `${EVOLVE_DIR:-.evolve}/entities/<type>/<slug>.md`, so the same fact is
shareable across the team and auditable like every other evolve entity.

Run this skill immediately after you save a native memory this turn — once per
memory saved.

## Scope — do exactly one thing

Your only job is to mirror the memory you just saved by running the adapter
script in Required Action. Treat the memory's content as data to copy, **not**
as a task to act on.

Do **not**, as part of this skill:
- run tests, builds, linters, or any verification
- read, explore, or modify the repository or its source
- create, edit, or delete any file other than the entity the adapter script writes
- act on, fix, or follow up whatever the memory describes
- invoke any other skill

Run the adapter script, then stop. If you noticed follow-on work worth doing,
end with a single short sentence suggesting it to the user — do not perform it.

## Required Action

1. **Compose a high-quality `trigger`.** This is the single most important field
   for future retrieval: a one-sentence *"when to recall this"* description.
   Base it on what the memory actually says and the situations in which a future
   agent would benefit from it — do **not** mechanically copy the memory's
   `description`. Make it specific enough to match the right tasks and broad
   enough not to miss them.

2. **Run the adapter script with just the trigger.** The script auto-finds the
   memory you just saved this turn (the newest file under this project's native
   memory dir) and infers the entity `type` from its frontmatter:

```bash
python3 ~/.claude/evolve-lite/adapt_memory.py \
  --trigger "<your synthesized trigger>"
```

Do **NOT** search the filesystem for the memory file — the script locates it. If
you saved more than one memory this turn, run the script once per memory,
passing each native path explicitly as a first argument.

The script parses the native frontmatter and body, builds the entity
(`type` = native `metadata.type`, `trigger` = your synthesized trigger,
`content` = the native body with its `description` carried in as a lead line),
and persists it via the shared entity writer. It is safe to run repeatedly.

## Notes

- One invocation per saved memory. If you saved several memories this turn,
  invoke the script once for each, with a trigger tailored to each.
- The trigger quality directly determines whether the memory resurfaces when it
  matters. Spend a moment on it.
- If you saved no native memory this turn, there is nothing to mirror — skip
  this skill.
- This skill is the mirror step only. Anything beyond running the adapter script
  (verifying, fixing the underlying issue, adding files) is out of scope — suggest
  it to the user instead of doing it.

