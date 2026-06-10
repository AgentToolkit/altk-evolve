---
name: agent-wiki-synthesize-skill
description: Read a normalized Claude Code trajectory JSON and produce a wiki-resident SKILL.md page that future agents can invoke. Use when a trajectory captured a non-trivial successful workflow worth promoting from a free-text guideline to an executable, callable artifact.
---

# Agent Wiki — Synthesize Skill

## Overview

Promote a successful workflow from a saved trajectory into an **executable
agent skill** living inside a wiki at `<wiki>/skills/<slug>/SKILL.md`. The
output is the procedural counterpart to `agent-wiki-extract-guidelines`'s
declarative pages: a guideline tells a future agent *what to do*; a synthesized
skill is a structured workflow page the future agent can read and *execute
directly*, optionally invoking sibling scripts via Bash.

This is the per-trajectory **promote-to-procedural** pass of the `agent-wiki`
family. Run it after one or more trajectories captured the same recipe and
you want future agents to invoke that recipe instead of re-deriving it.

> **Ingesting a whole batch end-to-end?** Prefer the `agent-wiki-ingest`
> skill, which sequences summarize → extract → synthesize → **consolidate**
> → catalog. It runs this skill at the right point (after extraction, before
> consolidation) and guarantees the consolidation pass that clusters the
> surviving atomics is never skipped. Use this standalone skill only to
> promote a single trajectory's workflow.

## When To Use

Use this skill when a trajectory captured:

- A **non-trivial successful workflow** — multiple tool calls, with at least
  one custom script or non-obvious sequence — that produced the answer after
  trial-and-error. The eventual happy path is worth saving.
- A **reusable command sequence or script** the agent wrote. Particularly
  if the agent had to reconstruct it across multiple attempts.
- A pattern a future agent will hit on a similar-but-not-identical task —
  parsing a binary format, walking a structured directory, reaching a
  specific tool fallback.

Skip this skill — let `agent-wiki-extract-guidelines` cover the case with a
guideline alone — when:

- The workflow is a single trivial command (`grep -c TODO ...`).
- The path embeds secrets, tokens, or one-off user inputs.
- A skill with the same trigger already exists in `<wiki>/skills/`.
- The session ended without reaching a clear successful answer.

## Input

A path that is either:

- a normalized trajectory JSON file
- a directory of such files

Default if no path is given:
`trajectories/normalized`.

## Workflow

### Step 1: Resolve input files

Use `Glob` to enumerate JSON files.

### Step 2: Glance at existing skills

`Glob <wiki>/skills/*/SKILL.md` to see what's already there. **Don't
re-author a skill with the same name** unless the trajectory's recipe
materially refines or generalizes it.

### Step 3: For each trajectory

Read the file. The fields you need:

- `session_id`, `agent`, `model`
- `openai_chat_completion.messages` — the source of truth for what happened

Walk the messages and identify:

#### 3a. The successful workflow

The **final, working** tool sequence — the one that produced the answer.
Distinguish it from the trial-and-error leading up to it. Capture the
exact tool calls, scripts, or command sequences verbatim.

#### 3b. The trial-and-error context

What didn't work — the dead ends. You'll use this to author a *trigger
description* so a future agent knows when to reach for this skill **instead
of** the failing approaches.

#### 3c. Environment assumptions

What was missing or had to be installed (no `exiftool`, `pip install
Pillow` needed, etc.).

If no clearly successful workflow is in the trajectory, output zero
skills for it and continue.

### Step 4: Decide a skill name and trigger

The skill **name** must be:

- kebab-case, action-oriented (`extract-jpeg-exif-camera-optics`,
  `parse-png-dimensions`, `walk-zip-central-directory`)
- specific enough that a future agent reading just the name can guess
  what it does
- not a duplicate of any existing skill in `<wiki>/skills/`

The skill **description** (one line in frontmatter) describes the *task*,
not the trajectory. Bad: "Solves the lens-model question from session
07d60d9f." Good: "Read camera-optics fields (lens model, focal length,
aperture, ISO) from JPEG EXIF using stdlib `struct` when system EXIF
tools are unavailable."

The **trigger** (frontmatter + `## When To Use`) describes the broad
task context, not the narrow original request.

### Step 5: Synthesize a JSON object

```json
{
  "name": "<kebab-case-name>",
  "description": "<one-line task description>",
  "trigger": "<situational context when this applies>",
  "session_id": "<from JSON>",
  "normalized_path": "<path to the JSON, relative to repo root>",
  "related_summary": "summaries/<sid>.md",
  "agent": "<from JSON, default 'claude-code'>",
  "tags": ["<2-4 short tags>"],
  "overview": "<1-2 sentences: what the skill does and when>",
  "when_to_use": [
    "<trigger condition 1>",
    "<trigger condition 2>"
  ],
  "workflow_steps": [
    "<step 1: an instruction to the future agent>",
    "<step 2: ...>"
  ],
  "scripts": [
    {
      "name": "<action>.py",
      "language": "python",
      "content": "<full script contents>"
    }
  ]
}
```

Notes on each field:

- **`overview`** — the SKILL.md's `## Overview` section body. Keep it
  to 1-2 sentences. Don't retell the original session.
- **`when_to_use`** — a bulleted list of trigger conditions. The
  future agent matches its current task against these.
- **`workflow_steps`** — the procedural body. Each step is an
  instruction the agent will follow. Reference scripts as
  `Run \`bash <wiki>/skills/<name>/scripts/<file>.sh\`` (the helper
  resolves `<wiki>` at write time).
- **`scripts`** — optional. If the workflow needs a non-trivial script,
  include it here. The helper writes it to
  `<wiki>/skills/<name>/scripts/<file>` and references it in the
  workflow body. Keep scripts minimal — strip incidental log lines or
  one-off args; replace literal file names with positional arguments.

### Step 6: Pipe the JSON to the helper

```bash
echo '<json>' | uv run python plugin-source/skills/agent-wiki/scripts/build_agent_wiki.py render-skill
```

Add `--rewrite` to overwrite an existing skill page.

The helper:

- Validates the JSON: `name` must be kebab-case, `description` and
  `workflow_steps` non-empty; sibling scripts must have `name` matching
  `^[\w.-]+$`.
- Writes `<wiki>/skills/<slug>/SKILL.md` with frontmatter (`name`,
  `description`, `trigger`, `agent`, `sources`, `related_summary`,
  `tags`, `verified_at`) and body (Overview, When To Use, Workflow,
  Sources).
- Writes `<wiki>/skills/<slug>/scripts/<file>` for each script, marks
  shell scripts executable.
- Updates `<wiki>/skills/_id_index.json` (skill slug → relpath).
- Appends `synthesize_skill` to `<wiki>/_audit.log` with session_id +
  slug.
- Skips silently if the skill already exists and `--rewrite` was not
  passed.

### Step 7: Refresh indexes

After processing all trajectories, run **once**:

```bash
uv run python plugin-source/skills/agent-wiki/scripts/build_agent_wiki.py catalog
```

This regenerates `<wiki>/skills/index.md`, the section indexes, and
`_index.jsonl` (which gains a `kind: "skill"` row per skill, sorted
between `cluster:` and `guideline:` rows). No LLM cost.

## Output structure

```
<wiki>/skills/
├── _id_index.json                     skill slug → relpath
├── index.md                           alphabetical listing (auto-generated)
└── <slug>/
    ├── SKILL.md                       the synthesized skill
    └── scripts/                       optional supporting scripts
        └── <action>.{sh,py}
```

The SKILL.md frontmatter shape:

```yaml
---
id: skill:<slug>
type: skill
name: <kebab-case-name>
description: <one-line task description>
trigger: <situational context>
agent: claude-code
sources:
  - <normalized_path>
related_summary: summaries/<sid>.md
verified_at: <date>
tags: [<tags>]
---
```

## Skills vs guidelines vs clusters

- **Guideline** (in `<wiki>/guidelines/`): the agent reads it and *decides*
  what to do. Free-text advice. Use when the lesson is conceptual.
- **Cluster** (in `<wiki>/guidelines/<slug>__cluster.md`): an aggregator
  page grouping related atomics. Recall-preferred over its members.
- **Skill** (in `<wiki>/skills/<slug>/SKILL.md`): a structured workflow
  page the agent reads and *executes*. Use when the lesson is a concrete,
  reusable recipe with a well-defined input/output.

At retrieval time, `_index.jsonl` lists all three kinds. Sort order is
`cluster` → `skill` → `guideline` → `task` so callable artifacts surface
first. The agent reads the SKILL.md, follows its Workflow section, and
invokes any sibling scripts via Bash.

## Best practices

1. **One skill per workflow.** Two unrelated successful workflows in one
   trajectory → two synthesize calls with different names.
2. **Cite the trajectory.** The helper records `sources` +
   `related_summary` automatically; you just need to set `session_id`
   and `normalized_path` correctly.
3. **Don't promote one-shots.** A skill is worth synthesizing only if
   the trigger is plausibly recurring. Single-use trajectories should
   stay as guidelines (or nothing at all).
4. **Don't paraphrase failure.** The skill describes what *worked*. If
   you're tempted to write "this skill avoids the problem where exiftool
   isn't installed," restate as "uses Pillow / stdlib struct; works in
   environments without system EXIF tools."
5. **Keep scripts minimal.** Strip log lines, debug prints, validation
   that wasn't actually exercised in the trajectory.
6. **Generality is everything.** A skill named `extract-gps-from-jpeg`
   will not match a lens-model query. If the trajectory only exercised
   one EXIF field, name the skill broadly (`extract-jpeg-exif-camera-optics`)
   so future agents recognize its applicability to siblings.
