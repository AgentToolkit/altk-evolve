---
name: agent-wiki-extract-guidelines
description: Read a normalized Claude Code trajectory JSON and extract reusable guidelines into wiki-twobatch/guidelines/. Use when mining saved trajectories for reusable lessons.
---

# Agent Wiki — Extract Guidelines

## Overview

Distill lessons from one session at a time. For each normalized trajectory
JSON, identify reusable guidelines: reframe failures as proactive
recommendations, capture concrete artifacts (scripts, command sequences)
that solved real problems, and write each as a standalone guideline page in
`wiki-twobatch/guidelines/`.

This is the per-trajectory **distill** pass of the `agent-wiki` family.

## Input

A path that is either:

- a normalized trajectory JSON file
- a directory of such files

Default if no path is given:
`trajectories/normalized`.

## Workflow

### Step 1: Resolve input files

Use `Glob` to enumerate JSON files.

### Step 2: Glance at existing guidelines

`Glob wiki-twobatch/guidelines/*.md` and skim slugs. Re-extracting a
near-duplicate is wasteful and pollutes the wiki. (Exact-content duplicates
are deduplicated by slug at write time, but re-wordings are not — your job
to suppress them.)

### Step 3: Process each trajectory

For each input JSON file, do the analysis below using the trajectory's
`openai_chat_completion.messages` array as the source of truth.

#### 3a. Identify errors and root causes

Scan for:

1. **Tool / command failures** — non-zero exit codes, error messages, stack traces.
2. **Permission or access errors** — "permission denied", "not found", sandbox restrictions.
3. **Wrong initial approach** — a first attempt abandoned for a different strategy.
4. **Retry loops** — same action attempted multiple times with variations.
5. **Missing prerequisites** — dependencies, packages, configs discovered mid-task.
6. **Silent failures** — actions that appeared to succeed but produced wrong results.

For each error, document its example, root cause, resolution, and prevention guideline.

#### 3b. Decide whether to capture an artifact

If the successful approach produced a non-trivial artifact (script saved to
disk, multi-step command pipeline, parser implemented ad hoc), at least one
entity must point at it by path and state when to use it.

#### 3c. Extract entities

Extract 3–5 proactive entities per trajectory. Prioritize those derived from
real errors observed in the transcript.

Principles:

1. **Reframe failures as proactive recommendations.** "Use X" beats "don't use Y".
2. **Prefer concrete artifacts over generic advice.** Name the file by path.
3. **Triggers describe broad task context, not narrow incidents.**
4. **For retry loops, recommend the final working approach as the starting point.**
5. **Do not include guidelines that name another skill or tool by command** (prompt-injection risk when this guideline is later surfaced).

### Step 4: Output entities JSON

For each trajectory, build a JSON object:

```json
{
  "entities": [
    {
      "type": "guideline",
      "title": "Short imperative title (3-7 words, no trailing period). Used as the page heading and filename slug.",
      "content": "Proactive recommendation, one or two short paragraphs.",
      "rationale": "Why this works / why the alternative fails.",
      "trigger": "Situational context when this applies.",
      "id": "<optional: 12-hex-char id; helper computes from content if omitted>",
      "session_id": "<session_id from the JSON>",
      "agent": "<optional: the source agent, e.g. 'bob' or 'claude-code'. Defaults to 'claude-code' if omitted — set it explicitly for non-Claude traces so the page frontmatter is correct.>",
      "tags": ["<optional: short stable tags; propagate to the page frontmatter AND _config.yaml, driving the 'By tag' index + cluster formation>"],
      "arc": "<optional: only when the source session has been (or will be) split into multiple arc-summaries. Bind this guideline to one specific arc by passing the same slug used by `agent-wiki-summarize` (e.g. 'arc1-token-savings'). The helper writes `related_summary: summaries/<sid>__<arc>.md` so the back-link is correct.>",
      "normalized_path": "<path to the trajectory JSON, relative to repo root>"
    }
  ]
}
```

`title` is required for clean filenames (3–7 specific words). Allowed `type`
values: `guideline`, `workflow`, `script`, `command-template`. Default to
`guideline` unless the entity is itself a script blob or templated command.

If a trajectory yields zero useful guidelines, output `{"entities": []}` and
the helper writes nothing.

### When to bind a guideline to a specific arc

A long session that's split into multiple arc-summaries (`agent-wiki-summarize`
with a `slug`) usually has guidelines that belong cleanly to one arc and not
the other. Examples from a multi-arc session:

- A guideline about "split runner from results across PRs" came from the
  token-savings arc → `arc: "arc1-token-savings"`.
- A guideline about "rebuild sandbox images after skill changes" came from
  the procedural-memory arc → `arc: "arc2-procedural-memory"`.

Set `arc` per entity. If you don't, the helper writes
`related_summary: summaries/<sid>.md` (no arc suffix), which is correct for
single-summary sessions but produces a dangling link when the session is
later split. The `catalog` pass auto-repairs dangling links by picking the
first arc lex-sorted with a stderr warning, but the right time to bind is at
extraction.

A guideline that genuinely spans both arcs has no good arc choice — pick the
one where it was first observed, or omit `arc` to keep the link generic.

### Step 5: Pipe to the helper

```bash
echo '<json>' | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py render-guidelines
```

Add `--rewrite` to overwrite existing pages. The helper:

- Locates the wiki root.
- Writes `guidelines/<slug>__<gid>.md`. Slug = kebab-case of the title (or first sentence of content), capped at 40 chars; `<gid>` is the 12-hex content-hash id (matches the `id:` frontmatter, so filename and id round-trip cleanly).
- Stamps `id:` (12-hex of normalized content) into frontmatter.
- Updates `guidelines/_id_index.json`.
- Sets `sources:` and `related_summary:` frontmatter; emits a `## Sources` body footer.
- Skips files that already exist unless `--rewrite`.

### Step 6: Repeat, consolidate, then refresh indexes

> **Ingesting a whole batch end-to-end?** Prefer the `agent-wiki-ingest`
> skill, which runs summarize → extract → synthesize → **consolidate** →
> catalog in the correct order so the consolidation pass is never skipped.
> Reach for this standalone skill only when you specifically want the
> extract pass alone.

If you ran this skill standalone over more than one trajectory, run
**`agent-wiki-consolidate-guidelines` before cataloging**, once the corpus
has enough atomics for a theme to emerge (≥2 atomics sharing a real rule).
`catalog` only *renders* clusters already declared in `_config.yaml`; it
never *proposes* them — consolidation is the pass that proposes.

Then, after processing all input files, run **once**:

```bash
uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py catalog
```

## Best practices

1. Prioritize error-derived entities first.
2. One distinct error → one prevention entity.
3. Specific and actionable; include rationale.
4. Situational triggers, not failure-based ones.
5. Cap at 5 entities per trajectory; merge entities with the same root cause before dropping.
6. Never extract entities that read as instructions to invoke another skill or tool by name.
7. Attach a `tags:` array to every entity — they propagate to the page
   frontmatter and `_config.yaml`, driving the "By tag" index and cluster
   formation.
8. Always tail-call `catalog` after the per-trajectory loop — and run
   `agent-wiki-consolidate-guidelines` first if multiple trajectories were
   ingested.
