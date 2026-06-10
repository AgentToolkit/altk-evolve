---
name: agent-wiki-tasks
description: Discover task families across summaries and write per-family comparison pages with findings narrative. Updates wiki-twobatch/_config.yaml task definitions and writes tasks/<slug>__task.md.
---

# Agent Wiki — Task Comparisons

## Overview

Two cognitive moves in one pass:

1. **Discover** — read across all summaries and identify task families
   (groups of sessions that attempted the same thing across trials and
   conditions).
2. **Compare** — for each family, write a `tasks/<slug>__task.md` page with a
   per-trial table and a findings narrative that calls out the
   experimental signal.

This is the cross-trajectory **analysis** pass of the `agent-wiki` family.

## When to run

- After enough summaries exist that a comparative pattern is visible
  (typically ≥3 sessions per family).
- When the experiment design (e.g. trial × condition matrices) explicitly
  cries out for a comparison page.

## Workflow

### Step 1: Read the corpus

```bash
uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py dump-summaries > /tmp/summaries.json
```

Output is a JSON array of one row per summary: `{session_id, goal, family,
trial, condition, tool_calls, errors, recall_used, summary_filename}`.
`family`, `trial`, `condition` come from existing classification rules —
they may be null if no rule has matched yet.

Read the file:

```
Read /tmp/summaries.json
```

### Step 2: Decide task families

For each candidate task family:

- **Slug**: kebab-case identifier (e.g. `extract-focal-length`).
- **Family**: short label used to group sessions (often equals slug, but
  can be looser e.g. `focal-length` for a slug `extract-focal-length`).
- **Family-match rules**: how a future session gets classified. Currently
  supported: `goal_substring: [list of substrings]`. A session matches
  the family if its `goal` contains any substring (case-insensitive).
- **Tags**: a few short tags.
- **Intro**: 1–2 sentences setting up the question.
- **Findings**: 2–5 bullets summarizing what the data shows. **This is
  the actual product** — a comparison page without findings is just a
  table.

Rules:

1. **A family needs ≥3 sessions.** Smaller groups should not get their own page.
2. **Findings must be evidence-grounded.** Cite tool-call counts, error counts, recall-used Y/N from the dump.
3. **Don't repeat what's in the table.** Findings should explain *why* the metrics differ, not restate them.
4. **Use overrides** for sessions whose `goal` doesn't auto-match. The override key in `_config.yaml/session_family_overrides` is the session id.

### Step 3: For each family, output JSON

```json
{
  "slug": "extract-focal-length",
  "title": "Extract focal length from JPEG EXIF",
  "family": "focal-length",
  "family_match": {
    "goal_substring": ["focal length"]
  },
  "intro": "Question template: *what focal length was used to take @sample.jpg?* FocalLength (tag 0x920A) and FocalLengthIn35mmFilm (tag 0xA405) live in the Exif sub-IFD.",
  "findings": "**Net signal:** the gap between IFD0/GPS-only scripts and the Exif sub-IFD is the dominant cost. Sessions whose recall pointed at a script that already covered the sub-IFD finished in 2-3 tool calls; sessions that had to write an inline parser took 5+.",
  "tags": ["exif", "focal-length", "comparison"]
}
```

Pipe to:

```bash
echo '<json>' | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py render-task
```

The helper:

- Updates `_config.yaml/tasks.<slug>` entry.
- Reads classified sessions; selects those matching `family`.
- Writes `tasks/<slug>__task.md` with the per-trial table + findings.

### Step 4: Add overrides if needed

If a session that *should* be in a family didn't classify automatically,
patch `_config.yaml`:

```bash
echo '{"session_family_overrides": {"<session-id>": {"family": "image-dims", "trial": 0, "condition": "claude_md_strong"}}}' \
  | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py update-config
```

### Step 5: Subtask pass — mandatory before refresh

Before refreshing indexes, scan the corpus for **subtask candidates**. The
default reflex of "the dataset is uniform, no subtasks needed" is wrong
for almost every dataset; even a 30-session benchmark of short workflows
typically has 4-6 subtask-worthy sessions. See "## Subtasks" below for
the heuristics + JSON contract + a worked example.

The minimum viable subtask layer for a condition × trial dataset: one
subtask per condition, anchored in the session that best demonstrates
that condition's distinctive behavior. Don't write 5 redundant subtasks
when 1 representative captures the pattern.

### Step 6: Refresh indexes

```bash
uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py catalog
```

This re-reads `_config.yaml`, re-classifies every summary, regenerates
each `tasks/<slug>__task.md`, scans `tasks/<slug>__subtask.md` files,
and regenerates `tasks/index.md` and the root `index.md`.

## Subtasks: per-session workstream pages

The `tasks/` directory holds *two* kinds of pages distinguished by filename
suffix:

- **`<slug>__task.md`** — cross-session task-comparisons (the workflow above).
- **`<slug>__subtask.md`** — narrative slices of a *single* session.

After Step 5 above, run a **second pass** to scan for subtask candidates.
Don't skip this just because the dataset is uniform — a 30-session benchmark
of short workflows still has 4-6 subtask-worthy sessions. The default
"there are no subtasks worth writing" reflex is wrong for almost every
dataset.

### When to propose a subtask

Treat each session in the corpus as a potential subtask candidate.
**Promote** to a subtask page when at least one of these is true:

1. **Exemplar of a condition or arc.** When the corpus has experimental
   conditions (`no_recall` / `guidelines` / `skill`, or arc-1 / arc-2),
   pick the session that best demonstrates *that condition's* distinctive
   behavior — its representative-best, representative-worst, or
   representative-failure trace — and write a subtask. Aim for one subtask
   per condition × dataset, not one per session.
2. **Multi-iteration debug arc.** A session where the agent retried 3+
   times against the same goal, with each iteration teaching something
   non-obvious (offset bugs, syntax gotchas, missing prerequisites). The
   subtask captures the debug walkthrough as a how-to.
3. **Recall miss / hit pattern.** A session where the recall layer
   surfaced material that turned out to be wrong, stale, or scope-mismatched
   — and the agent's recovery path is itself instructive.
4. **Workstream within a long arc-split session.** When a session has been
   split into multiple arc-summaries (`<sid>__arcN.md`), each arc usually
   has 1-3 internal workstreams worth their own subtask page (e.g. "split
   runner from results", "rebuild sandbox images", "walker fix for late
   bot batches"). Document each.

### When *not* to write a subtask

- The session is short and atomic — its `key_turns` already captures
  everything worth capturing.
- The lesson is already an atomic guideline. (A subtask is a *walkthrough*;
  a guideline is a *rule*. Same insight, different artifacts.)
- The session is one of N redundant repetitions of the same pattern. Pick
  the most illustrative; don't document all 5.

### Output JSON

```json
{
  "slug":              "<kebab-case-id, ideally including the source session prefix, e.g. multi-tool-dead-end-stack-66f11622>",
  "title":             "<short title; mention the session prefix and condition for context>",
  "parent_session_id": "<session_id>",
  "parent_summary":    "<filename inside summaries/, e.g. abc123.md or abc123__arc1.md>",
  "tags":              ["...", "<condition-name>", "<arc-slug>"],
  "narrative":         "<1-2 paragraphs framing the pattern; reference numerical cost (tool calls, errors, retries) when relevant>",
  "key_steps":         ["concrete step 1", "concrete step 2", "..."]
}
```

Pipe to:

```bash
echo '<json>' | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py render-subtask
```

Subtask pages are *authored* (not regenerated from `_config.yaml`). The
`catalog` pass picks them up, lists them in `tasks/index.md` under their
parent session, and adds rows to `_index.jsonl` with `kind: "subtask"`.

### Worked example: 4 conditions → 4 subtasks

When the dataset has 5 trials × 4 conditions, the simplest non-trivial
subtask layer is one subtask per condition, anchored in the session that
best demonstrates that condition's distinctive behavior. Concrete pattern
from `wiki-twobatch/`:

| Subtask | Condition | What it captures |
|---|:---:|---|
| Stdlib EXIF parser walkthrough | `seed` | Canonical stdlib path that *produces* the artifact later sessions recall |
| Multi-tool dead-end stack | `no_recall` | Worst-case 4-tool exhaustion before stdlib fallback |
| Recalled script path is stale | `guidelines` | Recall hit but stored paths missing → multi-retry recovery |
| Skill scope mismatch fallback | `skill` | Synthesized skill wrong for the question; inline anyway |

Pick one representative session per row; don't document every session.

## Best practices

1. **Findings is the product.** No findings → no task page.
2. **Three sessions minimum** before committing a task family.
3. **Tag families consistently.** `comparison` tag belongs on every task page.
4. **Leverage `condition` in your findings narrative** — it's the experimental variable.
5. **Subtasks need a parent_summary.** A subtask without a parent is just a
   short note — keep it inline in its parent summary's narrative instead.
6. Always tail-call `catalog` after any task or subtask loop.
