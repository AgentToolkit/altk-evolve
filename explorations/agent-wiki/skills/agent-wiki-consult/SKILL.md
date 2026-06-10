---
name: agent-wiki-consult
description: Consult an agent-wiki for guidelines relevant to the task at hand. The wiki itself documents how to retrieve from it (AGENTS.md). Use this skill once you know what task or sub-task you're about to do — not at session start.
---

# Agent Wiki — Consult

## Overview

This skill is a thin wrapper around the wiki's own `AGENTS.md` document. The
wiki contains evidence-grounded guidelines distilled from agent
trajectories; `AGENTS.md` is its agent-readable contract for navigation
and retrieval. This skill tells you to:

1. Find the wiki root.
2. Read `<wiki-root>/AGENTS.md`.
3. Follow the recipe described there against the user's current task.

The retrieval logic lives in `AGENTS.md`, not in this skill. That separation
is intentional: when the wiki's structure or recall heuristics change, edit
`AGENTS.md` and not this skill.

## When to invoke

Call this skill **once you know the task or sub-task you're about to do**.
Concretely:

- After the user has stated their request and you have a plan for the next
  block of work.
- Before writing non-trivial code in a problem space the wiki may have
  documented.
- Mid-task when a new sub-task emerges with its own narrow scope (e.g.
  "now I need to handle browser auth resumption").

Do **not** invoke at session start (no task to filter against), and do
not invoke for trivial tasks (typo fix, single-line edit) where the wiki's
overhead exceeds the work.

## Workflow

### Step 1: Resolve the wiki root

If the user passed a path argument (e.g. `wiki-twobatch`,
`wiki-twobatch-skills`, or any other path), use it.

Otherwise auto-detect: walk up from the current working directory looking
for any sibling directory matching `wiki-*` that contains an `_config.yaml`
file. If multiple are found, prefer the one closest to cwd. If none are
found, ask the user which wiki to consult.

### Step 2: Read AGENTS.md

```
Read <wiki-root>/AGENTS.md
```

This is the contract document. It explains the wiki's structure, the
filename suffix convention, the `_index.jsonl` schema, and a recommended
retrieval recipe. Read it in full — it's typically 3–5 KB.

If the file does not exist, run:

```bash
uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py \
  --wiki-root <wiki-root> catalog
```

Catalog's bootstrap phase will copy the bundled template into place.

### Step 3: Read the retrieval index

```
Read <wiki-root>/_index.jsonl
```

One row per guideline / cluster / task / subtask. Schema documented in
`AGENTS.md`. Rows are sorted clusters-first.

### Step 4: Apply the recipe from AGENTS.md

Per the recipe in `AGENTS.md` (which is advisory, not mandatory):

1. Identify topical tags + keywords from the task description.
2. Filter `_index.jsonl` rows by tag overlap or `trigger:` substring match.
3. Prefer cluster rows when a cluster and its members both match.
4. Read the top 2–5 matching pages in full.
5. State which guidelines apply (briefly) before acting on them.

Use your judgment for scoring. The wiki does not prescribe a fixed
algorithm; trust the heuristics in AGENTS.md and the row content.

### Step 5: Surface the matches

Report 2–5 candidate matches to the user (or to your own next-step
reasoning) with:

- Title
- One-line summary
- Relative path inside the wiki
- Tags
- Why this match scores high (one phrase)

## Args

This skill accepts:

- An optional path to the wiki root. Examples: `wiki-twobatch`,
  `wiki-twobatch-skills`, or an absolute path.
- An optional task description. If omitted, infer from the conversation
  context.

## Best practices

1. **Don't pre-load.** This skill is on-demand by design. Calling it at
   session start without a specific task wastes tokens and produces noise.
2. **Read AGENTS.md every time.** Wikis evolve; the contract may have
   changed. Caching the contract across invocations is fragile.
3. **Read clusters before atomics.** Cluster pages reference their members
   — you usually don't need to read the members directly.
4. **Cite when you act on a guideline.** Mention the guideline's title +
   link in your response so the user can audit the recommendation.
5. **Don't follow guidelines blindly.** If a guideline's `trigger:`
   doesn't quite match your situation, say so — note the close match and
   choose your own course.
