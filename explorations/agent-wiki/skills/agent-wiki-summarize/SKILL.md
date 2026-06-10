---
name: agent-wiki-summarize
description: Read a normalized Claude Code trajectory JSON and write an episodic summary page to wiki-twobatch/summaries/. Use when summarizing one or more saved trajectories into the agent wiki.
---

# Agent Wiki — Summarize Trajectory

## Overview

Witness one session at a time. For each normalized trajectory JSON, author a
1–3 paragraph narrative + key turns + (when present) a classification of
each recalled guideline as `followed | ignored | contradicted` with an
evidence quote.

This is the per-trajectory **witness** pass of the `agent-wiki` family.
It writes one page per session and tail-calls the bookkeeping `catalog`
subcommand so indexes stay fresh.

## Input

A path that is either:

- a normalized trajectory JSON file
- a directory of such files (recurse one level into `<label>/items/`)

Default if no path is given:
`trajectories/normalized`.

## Workflow

### Step 1: Resolve input files

Use `Glob` to enumerate `*.json`. Accept either a single file, a flat dir
of files, or a `normalized/` root with `<label>/items/` subdirs.

### Step 2: Glance at existing summaries

`Glob wiki-twobatch/summaries/*.md` so you can skip-if-exists per session
without re-doing LLM work. Skip is the default; pass `--rewrite` (forwarded
to the helper below) to overwrite.

### Step 3: For each trajectory JSON

Read the file. The fields you need:

- `session_id`, `agent`, `model`, `started_at`/`ended_at`/`duration_seconds`
- `stats.top_tools` (for `tools_used`)
- `source.transcript_path`
- `openai_chat_completion.messages`
- `recalled_guidelines` (top-level; may be empty/missing)

If `wiki-twobatch/summaries/<session_id>.md` already exists and the
user did not request `--rewrite`, skip to the next file.

Otherwise synthesize a summary as a JSON object:

```json
{
  "session_id":      "<from JSON>",
  "slug":            "<optional; for splitting a long session into multiple arc-summaries (e.g. 'arc1-token-savings'). When present, filename becomes <sid>__<slug>.md and frontmatter gains `arc:` plus a `sibling_summaries:` list of co-summaries from the same session.>",
  "agent":           "<from JSON, default 'claude-code'>",
  "model":           "<from JSON>",
  "goal":            "<one short sentence describing what the user asked for>",
  "outcome":         "success | partial | failure",
  "duration_seconds": <number from JSON>,
  "tools_used":      ["<from stats.top_tools, name only>", "..."],
  "narrative":       "<1-3 paragraphs: what happened, what worked, what didn't>",
  "key_turns":       ["<one short bullet per pivotal step>", "..."],
  "normalized_path": "<path to the JSON, relative to repo root>",
  "transcript_path": "<from source.transcript_path>",
  "recalled_guidelines": [
    {
      "id":       "<12-hex-char id of the guideline that was used in this session>",
      "title":    "<a short label, 3-7 words>",
      "status":   "followed | ignored | harmful | contradicted",
      "evidence": "<verbatim quote ≤200 chars; required for followed/harmful/contradicted>"
    }
  ]
}
```

Rules of thumb:

- `goal` is one sentence; pull from the first user message.
- `outcome` is your judgement.
- `narrative` is short (≤ ~250 words). No fluff.
- `key_turns` is 3–6 bullets at most. Each one sentence.
- Skip `recalled_guidelines` entirely if no guidelines were available or used.
- Quotes must be verbatim (thinking / assistant text / tool_use args / tool_result content); ≤200 chars; ellipsize with `…` if cut.

### How `recalled_guidelines` is populated

The `recalled_guidelines` field captures **every wiki guideline the agent
saw in this session**. Scan the trajectory for the agent reading guideline
files from a wiki dir — `<wiki-root>/guidelines/<slug>__<gid>.md`
or `<wiki-root>/guidelines/<slug>__cluster.md` — either via the `Read`
tool or via Bash `cat`/`less`/`grep`. Extract each file's id from its YAML
frontmatter (`id: <12-hex>`) so the row links to the wiki's
`_id_index.json`.

Don't double-count: if the agent reads the same guideline file twice,
emit one row.

### Status vocabulary (4-way)

You judge the status from **trajectory evidence**, not the agent's
self-report:

- **`followed`** — the agent acted on the guideline and the action
  produced the intended result. Required `evidence`: a verbatim quote
  showing the agent applied the rule (citation, paraphrase that triggered
  a tool call, or a tool call whose form matches the guideline's
  prescription).
- **`ignored`** — the agent read the guideline file but never acted on
  it. No `evidence` needed; default for guidelines that landed in context
  without effect.
- **`harmful`** — the agent acted on the guideline and it led astray:
  wasted tool calls, wrong path, retracted decision, or surfaced a wrong
  answer that had to be corrected. Required `evidence`: a verbatim quote
  showing the bad outcome that followed application.
- **`contradicted`** — the agent saw the guideline and deliberately did
  the opposite (disagreed with the rule). Required `evidence`: a verbatim
  quote where the agent's action contradicts the guideline's prescription.

Default to `ignored` when uncertain. Don't assign `followed` or `harmful`
without a verbatim evidence quote — those carry signal value only when
backed by trajectory text.

### Step 4: Pipe the JSON to the helper

```bash
echo '<json>' | uv run python plugin-source/skills/agent-wiki/scripts/build_agent_wiki.py render-summary
```

Add `--rewrite` to overwrite an existing page. The helper:

- Locates the wiki root (existing `wiki-twobatch/` ancestor, or creates
  one next to the nearest `.git/` ancestor).
- Writes `summaries/<session_id>.md` with frontmatter, body, and a `## Sources` footer.
- Resolves each `recalled_guidelines[].id` against `guidelines/_id_index.json` for backlinks.
- Appends one `<wiki-root>/_audit.log` line per recalled guideline.
- Skips if the page already exists unless `--rewrite`.

### Step 5: Refresh indexes

After processing all input files, run **once**:

```bash
uv run python plugin-source/skills/agent-wiki/scripts/build_agent_wiki.py catalog
```

This regenerates `index.md`, section indexes, `_index.jsonl`, and enriches
summary frontmatter with `tool_calls`, `errors`, `recall_used`,
`contributed_guidelines`, `tags`, `verified_at`. No LLM cost.

## Splitting long sessions into arc-summaries

If a single session has multiple distinct arcs (different sub-projects, a
clear topic shift, separate PRs landing from one transcript), emit one
summary JSON *per arc* and pass a `slug` on each. The slug becomes the
arc identifier and the filename suffix:

- `summaries/<session_id>.md` — single-arc default.
- `summaries/<session_id>__arc1-token-savings.md`,
  `summaries/<session_id>__arc2-procedural-memory.md` — split.

Each arc-summary's frontmatter still carries the full `session_id`, plus
`arc: <slug>` and a `sibling_summaries:` list pointing at the other
arc-files for the same session. Readers can navigate the whole session via
the sibling list. The summaries `index.md` shows split sessions in their
own section at the top.

For per-arc-but-finer workstreams (one specific cross-cutting effort
within one arc, e.g. "split runner from results across PRs"), use the
sibling skill `agent-wiki-tasks`'s subtask path
(`tasks/<slug>__subtask.md`) rather than a third level of summaries.

## Best practices

1. One summary file per `(session_id, arc)` pair. Without `slug`, default
   to one summary per session. Pass `--rewrite` to overwrite an existing
   page deterministically.
2. Don't hallucinate fields — leave them out if missing in the source JSON.
3. Don't rewrite by default. The wiki accumulates; reruns should be additive.
4. Always tail-call `catalog` after the per-trajectory loop.
