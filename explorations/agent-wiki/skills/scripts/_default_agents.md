# AGENTS.md — how an agent should read this wiki

This wiki is **evidence-grounded guidelines distilled from agent
trajectories**. Every page links back to the trajectory it came from, so any
recommendation is auditable and revisable.

You — the agent — should consult this wiki **once you know the task or
sub-task you are about to do**. Not at session start (too vague), not as a
last resort when stuck (too late). The right moment is after the user states
their request and you've decided what task family it belongs to, before you
start writing code.

## When to read me

Trigger conditions, any one of which should prompt a wiki check:

- You're about to author non-trivial code in a problem space the wiki has
  documented (build a CLI, parse a structured file format, automate a
  browser flow, design a TUI, run an experiment, ship a PR through review).
- The user mentions a topic that resembles entries in `_index.jsonl`'s
  `tags` or `trigger` fields.
- You're about to make an architectural choice (mode-as-subcommand vs
  options, env-var vs flag, cluster duplicates vs leave-as-is).
- A sub-task has been identified (you're now in the middle of a
  multi-step plan and the next step has its own narrow scope).

Don't read for trivial tasks (typo fix, single-line refactor) or topics
clearly outside the wiki's scope (the corpus is finite — see
`guidelines/index.md` for the topical surface).

## Structure

The wiki has three top-level sections, all under the wiki root:

```
<wiki-root>/
├── AGENTS.md          ← this file
├── index.md           ← human-friendly overview
├── _config.yaml       ← taxonomy: tags, clusters, tasks, family overrides
├── _index.jsonl       ← agent retrieval index (one row per page)
├── summaries/
│   ├── <session_id>.md                       ← single summary per session
│   └── <session_id>__<arc-slug>.md           ← multi-arc session split
├── guidelines/
│   ├── <slug>__<gid>.md                      ← atomic guideline (one rule); `<gid>` matches the `id:` frontmatter
│   ├── <slug>__cluster.md                    ← themed aggregator (recall-preferred)
│   └── _id_index.json                        ← guideline id → relpath
├── skills/
│   ├── <slug>/SKILL.md                       ← callable workflow page (recall-preferred over guidelines)
│   ├── <slug>/scripts/<file>                 ← optional supporting scripts (run via Bash)
│   └── _id_index.json                        ← skill slug → relpath
└── tasks/
    ├── <slug>__task.md                       ← cross-session comparison
    └── <slug>__subtask.md                    ← per-session workstream
```

**Filename suffixes are the navigation contract.** A page's role is decided
by its suffix; the wiki's tooling and other agents rely on it. Don't edit
the suffix.

## The retrieval index — read this first

`_index.jsonl` has one JSON object per line, one line per
guideline/cluster/skill/task/subtask page. The schema:

```json
{
  "kind": "guideline" | "cluster" | "skill" | "task" | "subtask",
  "id": "<12-hex-char content hash, OR cluster:<slug>, OR skill:<slug>, OR task:<slug>, OR subtask:<slug>>",
  "title": "<short title>",
  "tags": ["...", "..."],
  "trigger": "<situational context when this applies — empty for clusters and tasks>",
  "summary": "<one-paragraph snippet, ≤240 chars>",
  "link": "<relative path inside the wiki>",
  "cluster": "<slug if this guideline is a cluster member, else null>",
  "superseded_by": "<cluster page name when this atomic is part of a cluster>",
  "priority": "<\"high\" on cluster rows>",
  "members": ["<id>", "..."]   // on cluster rows
}
```

Rows are sorted **clusters first, then skills, then atomic guidelines, then
tasks**. Cluster pages are *aggregators* — when a cluster matches your
query, it references its member atomic guidelines; you usually don't need
to read the members directly unless you want the original wording or its
source trajectory.

**Skills** (`kind: "skill"`) live at `<wiki>/skills/<slug>/SKILL.md`.
They're callable workflow pages: a structured Overview / When To Use /
Workflow / (optional) supporting scripts under `<slug>/scripts/`. When a
skill row matches your task, prefer it over a same-trigger guideline —
the SKILL.md tells you exactly what to do (and may point at sibling
scripts you can run via Bash). Skills are **recall-preferred over
guidelines** because they're directly executable; an atomic guideline is
free-text advice you have to interpret.

## How to retrieve (advisory)

There's no mandated scoring algorithm. A reasonable recipe:

1. **Parse the user's request + your current task plan** for keywords +
   topical tags.
2. **Read `_index.jsonl`** end-to-end. It's small (typically 50–200 rows).
3. **Filter** rows whose `tags` overlap your topical tags, OR whose
   `trigger` substring-matches your task description.
4. **Prefer cluster pages** when both a cluster and its members match —
   the cluster gives you the consolidated rule plus links down. Each
   member's `superseded_by:` field tells you which cluster supersedes it.
5. **Read the top 2–5** matches (clusters + standalone atomics not
   superseded by any matched cluster). For each, follow the `link` and
   read the page body.
6. **Decide** which guidelines apply to your current task. State them
   briefly to the user before acting if helpful, especially when a
   guideline overrides what they asked for.

Your judgment is the scoring function. Don't read every row.

## Provenance

Every page links back to its source. When you cite a guideline in your
response or stake a non-trivial decision on one, the chain to follow is:

```
guideline.md
  ↓ frontmatter `related_summary:`
summaries/<session_id>[.md or __<arc>.md]
  ↓ frontmatter `sources:` (normalized JSON path + raw transcript path)
trajectories/<session_id>.json
  ↓ source.transcript_path
~/.claude/projects/.../<session_id>.jsonl
```

Cluster pages list their member atomic guidelines in their frontmatter
`members:` list and in the body's "## Members" section. Each member has
its own provenance — clusters don't replace member-level provenance, they
aggregate it.

## Worked example

User asks: *"I'm building a CLI tool with two modes (read and write)
plus a bunch of options. Should each mode be a subcommand or a flag?"*

Procedure:

1. **Task tags**: `cli`, `ux`, `architecture`, `subcommands`.
2. **Read `_index.jsonl`**. Filter for any row tagged `cli`, `ux`, or
   `workspace`.
3. Top hits (hypothetical):
   - `cluster:multi-subproject-workspace-conventions` (priority high; tags
     include `workspace`, `cli`, `conventions`).
   - `474bb2ba1076` "Promote a feature mode to a top-level flag, not an
     option" (atomic; tags include `cli`, `ux`, `workspace`).
4. **Prefer the cluster** — it consolidates several conventions including
   the mode-as-subcommand rule. Read
   `guidelines/multi-subproject-workspace-conventions__cluster.md`.
5. **Decide**: this confirms the user's question — promote each mode to a
   subcommand; demote everything else to options under it.
6. **Cite**: respond with the recommendation and (optionally) link the
   cluster page.

Total wiki tokens read: ~3 KB (one cluster page, plus a glance at one
atomic). Not a session-start preload; consult on-demand once the task is
clear.

## Bootstrapping notes

If `AGENTS.md` does not exist in a wiki, run
`uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py
--wiki-root <wiki-root> catalog` — the bootstrap pass copies the template
in. After bootstrap, this file is yours to edit; subsequent catalog runs
do not overwrite an existing `AGENTS.md`.

## Skill wrapper

`agent-wiki:agent-wiki-consult` is a thin wrapper that asks the agent to
follow this file's recipe against a given wiki root. Use the skill when
you want a one-step "consult the wiki" entry point; read this file
directly when you want to understand the contract.
