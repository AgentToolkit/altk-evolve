---
name: agent-wiki-ingest
description: Ingest one or more agent trajectories (raw bob/claude traces or normalized JSON) into an agent-wiki end-to-end — convert, summarize, extract guidelines, synthesize skills, optionally compare outcomes, consolidate into clusters, and catalog. Use when you have a batch of traces to turn into a wiki in one pass.
---

# Agent Wiki — Ingest (end-to-end orchestrator)

## Overview

This is the **one-pass entry point** for turning a batch of raw trajectories
into a fully-built wiki. It orchestrates the rest of the `agent-wiki` family
in the right order so no pass is skipped — in particular the
cross-trajectory **consolidation** pass, which is easy to forget when each
skill is invoked by hand.

You — the driving agent — run this by **spawning one subagent per
(trace × pass)**, not by doing the work inline. That keeps your own context
small (you never load every trace's full JSON) and lets independent passes
run in parallel. Each subagent acts as the corresponding single-purpose
skill (`agent-wiki-summarize`, `-extract-guidelines`, `-synthesize-skill`,
`-compare-outcomes`, `-consolidate-guidelines`); this skill only sequences
them and passes the per-trace adapter notes.

The pipeline:

```
0.  Convert    raw bob / claude traces → normalized analysis JSON   (skip if already normalized)
1.  Bootstrap  create wiki scaffold + seed catalog                  (skip if wiki exists)
1.5 Skip       drop traces whose summaries/<sid>.md already exists   [pre-flight — idempotency]
2.  Summarize  1 subagent / new-trace → summaries/<sid>.md          [PARALLEL]
3.  Extract    1 subagent / new-trace → guidelines/*.md (+tags)     [SEQUENTIAL]
4.  Synthesize 1 subagent / new-trace → skills/<slug>/ --archive-covered  [SEQUENTIAL]
4.5 Compare    success/failure contrasts → contrastive guidelines   [CONDITIONAL]
5.  Consolidate 1 subagent over the whole corpus → cluster pages    [SINGLE — MANDATORY]
6.  Catalog    final bookkeeping → indexes, used-by, priority       [you run this directly]
```

**Idempotent by default.** Re-running on the same source dir reprocesses
nothing: Step 1.5 filters out every trace that already has a summary page,
so Steps 2–4 only touch genuinely new traces. The consolidate + catalog tail
always runs (it's cheap and self-idempotent). To force a redo of an already-
ingested trace, keep it in the list and pass `--rewrite` to its `render-*`
calls.

**Why this order.** `synthesize-skill` runs *before* `consolidate-guidelines`
so skills claim recipe-level territory first (and archive the atomics they
cover via `--archive-covered`); consolidation then clusters only the
*surviving* atomics. This matches the consolidate skill's own rule — "don't
propose clusters that overlap a skill's territory."

**Why parallel vs sequential.** Summarize writes one independent file per
trace (`summaries/<sid>.md`) → safe to parallelize. Extract and synthesize
both mutate shared state (`guidelines/_id_index.json`, `skills/_id_index.json`,
`_config.yaml`, and the `_archived/` moves) → run them **one trace at a
time** to avoid lost-update races.

## Input

One of:

- a list of trace file paths
- a directory of traces (the skill globs it)
- already-normalized analysis JSON files

…plus a target `--wiki-root` (e.g. `wiki-twobatch-skills`).

### Detecting trace shape (Step 0 dispatch)

Read the top-level JSON keys of each input to classify it:

| Shape | Signature | Conversion |
|---|---|---|
| **bob session JSON** | top-level `sessionId` + `messages` | `bob-trace-converter` |
| **claude stream-json** | JSONL lines with `{"type":"system"/"assistant"/"result"}` | `normalize_stream_json_transcripts.py` |
| **normalized analysis JSON** | top-level `model` + `messages` + `metadata.id` | pass through (no conversion) |

## Step 0 — Convert

Write converted output under a stable corpus dir:
`trajectories/normalized/<label>/items/`.

**bob session JSON:**
```bash
NODE_OPTIONS='' node ~/.claude/skills/bob-trace-converter/scripts/convert_bob_trace.mjs \
  <trace.json> --out-dir trajectories/normalized/<label>/items --format both
```
> The `NODE_OPTIONS=''` prefix is required — some shells inject a `--require`
> preload that breaks a bare `node` invocation. Strip it for this call.

The converter writes three files per trace; the ingest pipeline consumes the
`*-openai-chat-completions.analysis.json` one.

**claude stream-json:**
```bash
uv run python explorations/agent-wiki/experiments/harness/normalize_stream_json_transcripts.py \
  --in <transcripts-dir> --out trajectories/normalized \
  --label <label> --user-prompt "<the task prompt>"
```

**Already normalized:** skip — use the path as-is.

Collect the resulting list of analysis-JSON paths; this is the trace set the
rest of the pipeline iterates.

## Step 1 — Bootstrap the wiki

If `<wiki-root>/_index.jsonl` does **not** exist:

```bash
mkdir -p <wiki-root>/{summaries,guidelines,tasks,skills}
uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py \
  --wiki-root <wiki-root> catalog
```

The first `catalog` seeds `AGENTS.md` and `_config.yaml` from the bundled
defaults and writes empty indexes. Skip this whole step if the wiki already
exists — you're appending to it.

### Piping JSON to the helper — avoid `echo`

Every `render-*` subcommand reads JSON on stdin. The `echo '<json>' | …`
form in the per-pass skills **breaks when the payload has multi-line
`content`/`narrative` fields** (literal newlines become invalid control
characters in the shell-quoted string). Tell every subagent to write its
payload to a temp file and `cat` it instead:

```bash
cat /tmp/ingest-payload.json | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py --wiki-root <wiki-root> render-guidelines
```

## Step 1.5 — Skip already-processed traces (pre-flight)

This is what makes re-running the skill on the same source dir cheap. The
helper's `render-*` subcommands skip-if-exists, but only *after* a subagent
has already read the trace and synthesized its output — so the LLM cost is
already spent. Filter **before** spawning any subagent.

For each normalized trace, read its `session_id` — it lives at
`metadata.id` (bob-converted analysis JSON) **or** top-level `session_id`
(claude-normalized). If `<wiki-root>/summaries/<sid>.md` already exists, the
trace was ingested on a prior run → drop it from the work-list. The
surviving **new-trace list** is what Steps 2–4 iterate.

Compute the new-trace list and log what was skipped (never let a silent
no-op masquerade as success):

```bash
for f in <trace-glob>; do
  sid=$(uv run python -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('session_id') or d.get('metadata',{}).get('id',''))" "$f")
  if [ -n "$sid" ] && [ -f "<wiki-root>/summaries/$sid.md" ]; then
    echo "skip (already ingested): $sid  $f"
  else
    echo "NEW: $sid  $f"
  fi
done
```

The `NEW:` lines are the work-list for Steps 2/3/4. If every trace is
skipped, that's fine — jump straight to Steps 5–6 (the tail always runs).

**Override.** To force reprocessing of an already-ingested trace, keep it in
the work-list and pass `--rewrite` to its `render-*` calls (the helper
overwrites instead of skipping).

## Step 2 — Summarize (parallel subagents)

Spawn **one subagent per new-trace** (from Step 1.5's work-list), **all in
parallel**. Each acts as
`agent-wiki-summarize` (point it at that skill's SKILL.md). In each subagent
prompt include:

- the analysis-JSON path and the `--wiki-root`
- the trace's **agent** (`bob`, `claude-code`, …) — it must set `agent:`
  accordingly, not hardcode `claude-code`
- the bob field-mapping adapter notes (only if the trace came from bob):
  `session_id` ← `metadata.id`; `model` ← top-level `model`; tool calls live
  in `messages[i].content[j]` blocks with `type: "tool_use"`;
  `transcript_path` ← `metadata.source_file`; `recalled_guidelines` is empty
  for a freshly-built wiki
- **do NOT run `catalog`** — the orchestrator runs it once at the end

Each subagent pipes its summary JSON to:
```bash
echo '<json>' | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py --wiki-root <wiki-root> render-summary
```

## Step 3 — Extract guidelines (sequential subagents)

Spawn **one subagent per new-trace, one at a time** (wait for each before
starting the next — they share `guidelines/_id_index.json` and
`_config.yaml`). Each acts as `agent-wiki-extract-guidelines`. In each
prompt:

- the analysis-JSON path, `--wiki-root`, `agent`, and bob adapter notes
- the list of **existing guideline slugs** (from prior traces this run) so it
  suppresses near-duplicates
- instruct it to attach a `tags:` array to every entity (these now propagate
  to both the `.md` frontmatter and `_config.yaml` — see commit that fixed
  `render-guidelines`)
- instruct it to set `"agent": "<source>"` on every entity. The
  extract-guidelines entity schema does **not** list `agent` as a field, so
  the subagent must add it explicitly; otherwise the page defaults to
  `agent: claude-code` even for bob traces.
- skip `arc:` for single-summary sessions
- **do NOT run `catalog`**

Pipe via:
```bash
echo '<json>' | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py --wiki-root <wiki-root> render-guidelines
```

## Step 4 — Synthesize skills (sequential subagents)

Spawn **one subagent per new-trace, one at a time** (shared `skills/_id_index.json`
plus `_archived/` moves). Each acts as `agent-wiki-synthesize-skill`. In each
prompt:

- the analysis-JSON path, `--wiki-root`, `agent`, bob adapter notes
- the list of **existing skill slugs** so it doesn't re-author one
- tell it to **decide promote-vs-skip** per that skill's "When To Use" rubric
  (trivial single-command recipes → skip and emit nothing)
- when promoting, pipe with `--archive-covered` so the atomics the skill
  subsumes are soft-archived:
  ```bash
  cat /tmp/skill-payload.json | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py --wiki-root <wiki-root> render-skill --archive-covered
  ```
  `--archive-covered` is safe to run blind: the matcher only archives an
  atomic from *another* trajectory when the skill's tags are a true superset
  (≥2 non-generic shared tags). The weak lexical heuristics (a slug word or
  format token appearing in the atomic's title) fire only for atomics from
  the **same trajectory** this skill was synthesized from, so a skill can no
  longer reach across into an unrelated trace's atomic on a coincidental
  word like "python" or "csv".
- **do NOT run `catalog`**

## Step 4.5 — Compare outcomes (conditional)

Run this step when the corpus has multiple trajectories that can be judged as
successes and failures for the same or similar task — benchmark corpora,
repeated attempts, A/B experiment arms, or user-labeled sessions. **Skip it
when there is no success/failure contrast**: a corpus of only apparent
successes still produces summaries, atomics, skills, and clusters, but it
cannot safely derive contrastive rules.

Spawn **one** subagent acting as `agent-wiki-compare-outcomes` over the whole
corpus (point it at that skill's SKILL.md). It derives *contrastive*
guidelines — rules backed by a failed path, a successful path, and concrete
trajectory evidence — rather than mining from one trajectory alone. It does
not depend on benchmark-specific outcome labels; it can LLM-judge success or
failure from the normalized transcript. In its prompt:

- the `--wiki-root` and the trace `agent:` value (so promoted contrastive
  guidelines are stamped with the right source, not defaulted to `claude-code`)
- build the evidence pack:
  ```bash
  uv run python explorations/agent-wiki/skills/agent-wiki-compare-outcomes/scripts/compare_outcomes.py \
    --input <normalized-dir-or-json> \
    --out-json /tmp/agent-wiki-outcome-comparison.json \
    --out-md <wiki-root>/tasks/outcome-comparison.md \
    --judge-outcomes always
  ```
  Prefer `--judge-outcomes always` when stored labels come from a benchmark
  evaluator or other dataset-specific schema; use `--judge-outcomes missing`
  only for trusted, dataset-neutral labels.
- instruct it to promote **only** strong candidates (one failed + one
  successful run in the same group, a task-action tool/API or workflow
  difference, source trajectory IDs for both sides) per that skill's "Inspect"
  and "Promote Carefully" rules — keep weak ones as hypotheses, not rules.
- pipe promoted entities through the helper (avoid `echo`; use a temp file):
  ```bash
  cat /tmp/contrastive-guidelines.json | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py --wiki-root <wiki-root> render-guidelines
  ```
- **do NOT run `catalog`** — the orchestrator runs it once at the end

Run this **after** synthesize and **before** consolidate, so the new
contrastive guidelines can participate in clusters.

## Step 5 — Consolidate (single subagent — MANDATORY)

**This pass is not optional.** It is the step most easily forgotten when the
family is invoked by hand, and it is the whole reason this orchestrator
exists. Always run it, even on a small corpus — the subagent's own judgment
returns zero clusters when nothing qualifies, which is the correct outcome
for a tiny or heterogeneous corpus.

**Run it even when Step 1.5 skipped every trace.** A re-run that ingests no
new traces still benefits from a consolidation pass over the existing
corpus — it can form clusters that an earlier run missed. Steps 5 and 6 are
the always-on tail; only Steps 2–4 are gated on the new-trace list.

Spawn **one** subagent acting as `agent-wiki-consolidate-guidelines` over the
whole surviving-atomic corpus. In its prompt:

- the `--wiki-root`
- instruct it to run `dump-guidelines` first, then propose clusters
- remind it: a cluster needs ≥2 atomic members sharing a real **rule** (not
  just a topic); don't propose clusters overlapping a skill's territory (the
  skill is already the canonical aggregator)
- **do NOT run `catalog`** — the orchestrator runs it next

Each cluster is piped via:
```bash
echo '<json>' | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py --wiki-root <wiki-root> render-cluster
```

## Step 6 — Catalog (you run this directly)

One final bookkeeping pass:
```bash
uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py --wiki-root <wiki-root> catalog
```

This regenerates `_index.jsonl`, the section indexes, the priority table,
the "By tag" and used-by sections, and propagates `cluster:` /
`superseded_by:` backrefs onto clustered atomics.

## Report

After Step 6, report the end-state counts: summaries, surviving atomics,
clusters, skills, archived atomics — and call out any trace that produced no
guidelines or no skill (trivial recipes), plus any cluster proposals that
were considered and rejected.

## Best practices

1. **Consolidation is mandatory.** Step 5 always runs. The cluster subagent
   self-skips individual clusters; the *pass* never skips.
2. **One subagent per (trace × pass).** Don't batch multiple traces into one
   subagent — it bloats context and muddies provenance.
3. **Parallel only for summarize.** Extract, synthesize, and consolidate all
   touch shared index/config state — keep them sequential.
4. **Subagents never `catalog`.** Only the orchestrator does, once, at the
   end. A mid-run catalog wastes work and can race with in-flight writes.
5. **Pass `agent:` through.** Bob traces are `bob`, not `claude-code`. The
   summarize and extract subagents must stamp the right source.
6. **Tags on every guideline.** They drive the "By tag" index and future
   cluster formation; an untagged atomic is invisible to tag-based recall.
7. **Idempotent by default.** Step 1.5 skips any trace that already has a
   `summaries/<sid>.md`, so re-running on the same source dir reprocesses
   nothing. Use `--rewrite` on the `render-*` calls to force a redo of a
   specific trace.
