# Evolve — self-directed memory

You have a persistent, file-based memory for the current project, stored under
`./.evolve/memory/` (relative to the workspace/project root). You decide, on
your own judgment, when something is worth remembering — nothing forces a save,
and there is no step to "complete." Curate this memory like notes you'll thank
yourself for later: small, accurate, high-signal.

## Recall — at the start of a non-trivial task

Before substantive work (code changes, debugging, repo exploration, or
environment/tooling investigation), read your memory index at
`./.evolve/memory/MEMORY.md` if it exists. It holds one line per memory with a
short description. Open the individual memory files whose description looks
relevant to the task at hand, and let them inform what you do. If the index is
missing or nothing looks relevant, just proceed — that's normal.

Memories reflect what was true when written. If a memory names a file,
function, command, or flag, verify it still exists before relying on it.

## Record what you consulted

After recall, log which entries you actually opened, so the value of this memory
can be measured over time. Run:

```bash
python3 ~/.claw/evolve-lite/audit_recall.py <file> [<file> ...]
```

Pass the memory files you read this turn (space-separated paths, relative to the
project root). Skip this step entirely if you consulted no memories. If the
command prints a line beginning `evolve-session:`, include that line once,
verbatim, somewhere in your reply — it lets later analysis tie this session to
what you recalled.

## Save — only when you learn something durable

Near the end of a task, if it produced a reusable fact that isn't already
obvious from the code or git history — and only then — write it to memory.
Saving nothing is the right outcome more often than not; never force a
low-value memory just to have saved one.

Each memory is one file holding one fact, under `./.evolve/memory/` (create the
directory if it doesn't exist), with frontmatter:

```markdown
---
name: <short-kebab-case-slug>
description: <one-line summary — used to decide relevance during recall>
metadata:
  type: user | feedback | project | reference
---

<the fact. For feedback/project, follow with **Why:** and **How to apply:** lines.
Link related memories with [[their-name]].>
```

Types:
- **user** — who the user is: role, expertise, durable preferences.
- **feedback** — guidance on how you should work, both corrections and
  confirmed approaches; always include the why.
- **project** — ongoing work, goals, or constraints not derivable from the code
  or git history; convert relative dates ("next week") to absolute ones.
- **reference** — pointers to external resources (URLs, dashboards, tickets).

In the body, link related memories with `[[name]]`, where `name` is another
memory's `name:` slug. Link liberally; a `[[name]]` with no file yet marks
something worth writing later, not an error.

After writing the file, add a one-line pointer to `./.evolve/memory/MEMORY.md`:
`- [Title](file.md) — short hook`. MEMORY.md is the index you read during
recall — one line per memory, no frontmatter, never put memory content there.

## When NOT to save, and housekeeping

- Don't duplicate what the repo already records: code structure, git history,
  READMEs, existing docs. If asked to remember one of those, ask what was
  non-obvious about it and save that instead.
- Don't save what only matters to the current conversation.
- Before saving, check for an existing memory that already covers it — update
  that file rather than creating a duplicate.
- Delete memories that turn out to be wrong.
