# Agent-wiki: design & rationale

*A durable, evidence-grounded knowledge layer mined from an agent's own
trajectories, consulted by future agents at recall-time.*

This doc explains **why** the agent-wiki is shaped the way it is, **what**
its pieces are, **how** a raw trace becomes a recallable page, and **what
the experiments show**. It is the canonical design statement; for the
operational contracts it links to the recall recipe
([`_default_agents.md`](../skills/scripts/_default_agents.md),
copied into every wiki as `AGENTS.md`), and the empirical log
([`experiments/RESULTS-SUMMARY.md`](../experiments/RESULTS-SUMMARY.md)).

---

## 1. The problem

Coding agents start every session cold. An agent that spent twenty tool
calls last week discovering that a Debian container has no `pip` and
PEP-668 blocks `pip install` will spend twenty tool calls rediscovering it
next week. The knowledge a session produces dies with the session.

The usual fixes don't hold up:

- **Hand-authored runbooks** drift from reality and carry no provenance —
  you can't tell whether a rule still reflects how the tool behaves, or who
  decided it.
- **Raw trajectory stores** keep everything but generalize nothing. They're
  too bulky to load at recall-time, and a future agent has to re-derive the
  lesson from a transcript instead of reading it.
- **Generic long-term memory** (embed-everything vector stores) is lossy and
  unauditable: a retrieved snippet has no chain back to the moment it was
  true.

The goal: a **knowledge layer the agent earns from its own work** — small
enough to consult cheaply, general enough to apply to unseen-but-related
tasks, and auditable down to the transcript that produced each claim.

## 2. The core idea

Build a **wiki from agent traces**. Each completed trajectory is distilled
into pages; every page links back to the session it came from. Future agents
**consult the wiki once they know the task they're about to do** — after the
user's request is understood and the task family is clear, before writing
code.

```
 past sessions            the wiki                  future session
┌──────────────┐      ┌──────────────────┐       ┌──────────────────┐
│ trajectory A │─┐    │ summaries/       │       │ user states task │
│ trajectory B │─┼──▶ │ guidelines/      │ ◀─────│ agent reads      │
│ trajectory C │─┘    │ skills/  tasks/  │consult│ _index.jsonl,    │
└──────────────┘ dist.│ _index.jsonl     │       │ applies the rule │
        ▲             └──────────────────┘       └──────────────────┘
        └── provenance ──┘
   (each wiki page links back to the trajectory it was distilled from)
```

The wiki is **not** a transcript archive and **not** a session-start
preload. It's a curated, recall-preferred index of distilled lessons that an
agent pulls from on demand.

## 3. Design principles

Each decision below earns its place; the *why* is the point.

### Provenance is mandatory

Every page is traceable, in a couple of clicks, to the raw transcript that
produced it:

```
guideline.md
  ↓ related_summary:
summaries/<session_id>.md
  ↓ sources:
trajectories/<session_id>.json
  ↓ source.transcript_path
~/.../<session_id>.jsonl   (the raw trace)
```

Why: a recommendation is only trustworthy if you can audit where it came
from and revise it when the underlying tool behavior changes. Provenance is
what separates this from a generic memory store. Cluster pages aggregate
their members' provenance rather than replacing it.

### Page kinds, and a retrieval preference order

The wiki has five page kinds, and `_index.jsonl` sorts them in **recall
preference order**:

| Kind | What it is | Why it exists |
|---|---|---|
| **cluster** | Themed aggregator over ≥2 atomic guidelines | One consolidated rule instead of N near-duplicate hits |
| **skill** | Callable workflow page + sibling scripts | Directly *executable* — no interpretation needed |
| **guideline** (atomic) | One rule, free-text, trigger-tagged | The base unit; a single distilled lesson |
| **task / subtask** | Cross-session comparison / per-session workstream | Analysis surface, not recall-time advice |
| **summary** | Episodic record of one session | The provenance anchor every other page links to |

Sort order is `cluster → skill → guideline → task`, so the most
consolidated and most directly-actionable artifacts surface first. The exact
retrieval recipe (parse task → read `_index.jsonl` → filter by tag/trigger →
prefer clusters → read top 2–5) lives in the recall contract; see
[`_default_agents.md`](../skills/scripts/_default_agents.md).

### Procedural over declarative where possible

A **guideline** tells a future agent *what to do* ("when pip's module dir is
missing, don't trust `ensurepip`"). A **skill** is a structured workflow page
the agent can *execute* — Overview / When-To-Use / Workflow / optional
sibling scripts it runs via Bash.

Skills are **recall-preferred over guidelines** because they remove an
interpretation step: the agent reads the SKILL.md and runs the recipe
instead of reconstructing it from advice. §5 shows skills also win on cost.

### Consolidation + delete-on-promote

Two cross-trajectory moves keep the recall surface small and non-redundant:

- **Consolidation** clusters ≥2 atomic guidelines that share a real *rule*
  (not merely a topic) into a `__cluster.md` aggregator. Members stay on
  disk with a `superseded_by:` backref — provenance is preserved.
- **Delete-on-promote** (`--archive-covered`): when a skill is synthesized
  (or a cluster created), the atomics it subsumes are **soft-archived** to
  `_archived/`. They leave the recall index but stay auditable on disk; the
  `_audit.log` records the move.

Why: §5's central empirical finding is that **recall quality degrades as the
index grows** — a smaller, non-redundant index helps even on tasks where no
page matches. Consolidation and pruning are how the wiki stays small as it
accumulates traces.

### Recall-time discipline

Consult **once you know the task or sub-task** — not at session start (too
vague to match), not as a last resort when stuck (too late). And the
**pointer wording is load-bearing**: a strong-imperative instruction to
consult the wiki gets followed; a soft "you may want to check" gets skipped
(§5, the A/B sweep). The pointer lives in the workspace `CLAUDE.md` /
`AGENTS.md`; placement and wording both matter.

## 4. How a trace becomes a recallable page

The build pipeline is a sequence of LLM passes, each piping structured JSON
to a deterministic builder
([`build_agent_wiki.py`](../skills/scripts/build_agent_wiki.py))
that writes the page and maintains the indexes:

```
raw trace ─┬─[convert]──▶ normalized JSON
           │
           ├─[summarize]─────────▶ summaries/<sid>.md        render-summary
           ├─[extract-guidelines]▶ guidelines/<slug>__<gid>.md  render-guidelines
           ├─[synthesize-skill]──▶ skills/<slug>/SKILL.md     render-skill --archive-covered
           │                                                  (per trace, above)
           ├─[consolidate]───────▶ guidelines/<slug>__cluster.md  render-cluster
           │                                                  (once, cross-corpus)
           └─[catalog]───────────▶ _index.jsonl, indexes, backrefs
```

| Stage | Skill | Builder subcommand | Scope |
|---|---|---|---|
| Convert | (bob-trace-converter / `normalize_stream_json_transcripts.py`) | — | per trace |
| Summarize | [`agent-wiki-summarize`](../skills/agent-wiki-summarize/SKILL.md) | `render-summary` | per trace |
| Extract guidelines | [`agent-wiki-extract-guidelines`](../skills/agent-wiki-extract-guidelines/SKILL.md) | `render-guidelines` | per trace |
| Synthesize skill | [`agent-wiki-synthesize-skill`](../skills/agent-wiki-synthesize-skill/SKILL.md) | `render-skill` | per trace |
| Consolidate | [`agent-wiki-consolidate-guidelines`](../skills/agent-wiki-consolidate-guidelines/SKILL.md) | `render-cluster` | **cross-corpus, once** |
| Catalog | (any) | `catalog` | bookkeeping |

**Order matters.** `synthesize-skill` runs *before* `consolidate` so skills
claim recipe-level territory first (and archive the atomics they cover);
consolidation then clusters only the surviving atomics. This matches the
consolidate skill's own rule — don't propose a cluster overlapping a skill's
territory.

**`catalog` renders; `consolidate` proposes.** A sharp edge worth
internalizing: `catalog` only *materializes* clusters already declared in
`_config.yaml` and refreshes indexes/backrefs. It never *proposes* new
clusters. Consolidation is the LLM pass that proposes them. Running `catalog`
and expecting clusters to appear is a mistake — they won't unless
consolidation declared them first.

### The one-pass entry point

[`agent-wiki-ingest`](../skills/agent-wiki-ingest/SKILL.md)
orchestrates the whole pipeline end-to-end (convert → bootstrap → summarize
→ extract → synthesize → consolidate → catalog) via subagent fan-out:
summarize runs in parallel (independent file writes), extract and synthesize
run sequentially (they mutate shared index/config state), consolidation runs
once. It exists specifically so the **consolidation pass is never silently
skipped** when ingesting a batch — the failure mode that motivated it.

### Build patterns

The same corpus can be turned into a wiki three ways, varying *when* the
wiki is built and *what* the agent sees during each trial (see
[`RESULTS-SUMMARY.md` §3–4](../experiments/RESULTS-SUMMARY.md)):

- **Open-loop** — trials run against a fixed external wiki; the new wiki is a
  study log built from observing them.
- **Closed-loop** — trials mount the wiki being built; it grows trial-by-trial,
  so trial N+1 sees what trial N spawned. The only pattern with real
  intra-wiki recall data.
- **Retroactive** — the wiki stays empty during all trials, then is built in
  one batch afterward. Cleanest pure-recipe corpus.

The three real-task themes emerge in **all three** patterns — consolidation
is robust to build order.

## 5. Evidence

All experiments use the same 16-task corpus, `claude_md_strong` pointer,
3 trials/task. `total_cost_usd` is the ground-truth cost metric (cache reads
bill at ~10% of regular input, so raw token sums overcount). Full tables and
methodology: [`experiments/RESULTS-SUMMARY.md`](../experiments/RESULTS-SUMMARY.md).

| Finding | Result | Source |
|---|---|---|
| **Wiki vs no wiki** | −20% cost, −38% duration, −43% tool calls, accuracy unchanged (96%) | [twobatch-comparison](../experiments/twobatch-comparison.md) |
| **Pointer wording is load-bearing** | strong-imperative CLAUDE.md 3/3 reads; soft phrasing 1/3 | [RESULTS-SUMMARY §1](../experiments/RESULTS-SUMMARY.md#1-agentsmd-ab-sweep-the-original) |
| **Build pattern is robust** | same 3 clusters emerge open-/closed-/retroactive | [RESULTS-SUMMARY §3–4](../experiments/RESULTS-SUMMARY.md#34-build-pattern-comparison-closed-loop-vs-retroactive) |
| **Skills > guidelines** | skills-only $0.146 vs guidelines $0.17 (−14%), accuracy 98% vs 96% | [twobatch-skills-comparison](../experiments/twobatch-skills-comparison.md) |
| **Composition is non-additive** | skills+guidelines costs +22% vs skills, +5% vs guidelines | [twobatch-fourway-comparison](../experiments/twobatch-fourway-comparison.md) |
| **Composition > size; skills-only still cheapest** | delete-on-promote (corrected index): −3% vs both, +18% vs skills | [twobatch-fiveway-comparison](../experiments/twobatch-fiveway-comparison.md) |

The throughline across these:

- **The wiki materially reduces cost at equal accuracy.** Savings come
  mainly from fewer tool calls and shorter responses, not from reading fewer
  input bytes — the agent reads *more* wiki bytes but acts more directly.
- **A smaller recall surface helps even when nothing matches.** The
  skills-only arm beat guidelines-only on tasks where *no skill matched*
  (e.g. t2-imports −39%) — evidence that index noise itself costs, which is
  why consolidation and delete-on-promote exist.
- **Don't stack page kinds.** Skills + guidelines together is the worst
  populated wiki, and pruning the redundant atomics doesn't recover the gap.
  Pick procedural-first; let consolidation + archive keep the rest lean.

## 6. Open questions / limitations

From [`RESULTS-SUMMARY.md`](../experiments/RESULTS-SUMMARY.md)'s open
questions — live, not yet resolved:

- **Statistical power.** Headline numbers rest on 3 trials/task; per-task
  confidence intervals are wide, especially on the two observed regressions
  (wav-info, imports).
- **True transfer.** All experiments reuse the same task in build and recall.
  A real transfer test (build from tasks Y, recall on task X where X ∈
  family(Y), X ∉ Y) would test whether clusters *generalize* rather than
  memorize.
- **Scale.** 16 tasks is small. Does the cost-reduction percentage hold,
  grow, or saturate at 50+ tasks and a larger index?
- **Why composition regresses.** The skills+guidelines penalty is
  output-token-driven, not read-count-driven — trace-level inspection of why
  the agent "says more" when both kinds are present is unresolved.

## See also

- [`schema.md`](schema.md) — the on-disk schema reference: directory layout, per-kind frontmatter, links, and the promotion/archival lifecycle.
- [`_default_agents.md`](../skills/scripts/_default_agents.md) — the recall contract copied into every wiki as `AGENTS.md` (page kinds, retrieval recipe, provenance chain).
- [`experiments/RESULTS-SUMMARY.md`](../experiments/RESULTS-SUMMARY.md) — the full empirical log.
- The `agent-wiki-*` skills under [`skills/`](../skills/) and the builder [`build_agent_wiki.py`](../skills/scripts/build_agent_wiki.py).
