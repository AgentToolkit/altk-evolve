# Evolve — self-directed memory

You have a persistent, file-based memory for the current project, stored as
*entities* under `./.evolve/entities/<type>/` (relative to the workspace/project
root). Each entity is one fact; "memory" and "entity" are the same thing. You
decide, on your own judgment, when something is worth remembering — nothing
forces a save, and there is no step to "complete." Curate this store like notes
you'll thank yourself for later: small, accurate, high-signal.

## Recall — your first action, before any other tool use

On a non-trivial task (code changes, debugging, repo exploration, or
environment/tooling investigation), your FIRST action — before reading source,
running commands, or anything else — is to check `./.evolve/entities/`: list
that directory, read each entity's `trigger` line, and open the entity files
whose trigger matches the task. The moment you open one or more entities, your
next step — still part of this same first action — is to record them with the
audit command under "Record what you consulted" below; do it before you move on
to the task. Let what you find inform the work that follows. If the directory is
missing or nothing matches, note that and proceed — that's normal.

Entities reflect what was true when written. If one names a file, function,
command, or flag, verify it still exists before relying on it.

## Record what you consulted

Whenever you opened entities in the recall step above, record them now — run
this before doing anything else, so the value of this memory can be measured
over time:

```bash
python3 ~/.codex/evolve-lite/audit_recall.py <id> [<id> ...]
```

Pass the entity id `<type>/<name>` for each entity you consulted, where `<type>`
is its directory under `entities/` and `<name>` is its filename without `.md`
(e.g. `project/test-fixture-generated`). Skip this step entirely if you
consulted no entities. If the command prints a line beginning `evolve-session:`,
include that line once, verbatim, somewhere in your reply — it lets later
analysis tie this session to what you recalled.

## Save — only when you learn something durable

Near the end of a task, if it produced a reusable fact that isn't already
obvious from the code or git history — and only then — write it as an entity.
Saving nothing is the right outcome more often than not; never force a
low-value entity just to have saved one.

Each entity is one file holding one fact, at
`./.evolve/entities/<type>/<short-kebab-slug>.md` (create the directory if it
doesn't exist — `<type>` is one of the types below). The filename is the
entity's name; the frontmatter carries its type and trigger:

```markdown
---
type: <user | feedback | project | reference>
trigger: <one line naming the situation in which a future session should recall this>
---

<the fact. For feedback/project, follow with **Why:** and **How to apply:** lines.
Link related entities with [[their-name]].>
```

The `trigger` is what a future session matches against during recall, so make it
about *when* the fact applies, not just what it is.

Types (the `<type>` directory and frontmatter value):
- **user** — who the user is: role, expertise, durable preferences.
- **feedback** — guidance on how you should work, both corrections and
  confirmed approaches; always include the why.
- **project** — ongoing work, goals, or constraints not derivable from the code
  or git history; convert relative dates ("next week") to absolute ones.
- **reference** — pointers to external resources (URLs, dashboards, tickets).

In the body, link related entities with `[[name]]`, where `name` is another
entity's filename slug. Link liberally; a `[[name]]` with no file yet marks
something worth writing later, not an error.

## When NOT to save, and housekeeping

- Don't duplicate what the repo already records: code structure, git history,
  READMEs, existing docs. If asked to remember one of those, ask what was
  non-obvious about it and save that instead.
- Don't save what only matters to the current conversation.
- Before saving, check for an existing entity that already covers it — update
  that file rather than creating a duplicate.
- Delete entities that turn out to be wrong.
