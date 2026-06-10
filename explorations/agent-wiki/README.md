# agent-wiki

An exploration in turning agent trajectories into a **reusable, evidence-grounded
wiki** that future agents consult before acting — and the experiments measuring
whether it actually helps.

The core idea: after an agent finishes a task, distill its trajectory into wiki
pages — episodic **summaries**, atomic **guidelines**, themed **cluster** pages,
and executable **skills** — each linked back to the trajectory that produced it.
A future agent, pointed at the wiki's `AGENTS.md`, retrieves the pages relevant
to its task and applies them instead of re-deriving the recipe.

## Layout

```
explorations/agent-wiki/
├── skills/            the agent-wiki skill family + the build_agent_wiki.py builder
│   ├── agent-wiki-summarize/             trajectory → episodic summary
│   ├── agent-wiki-extract-guidelines/    trajectory → atomic guidelines
│   ├── agent-wiki-synthesize-skill/      trajectory → executable SKILL.md
│   ├── agent-wiki-consolidate-guidelines/ atomics → themed cluster pages
│   ├── agent-wiki-tasks/                 cross-session task-comparison pages
│   ├── agent-wiki-consult/               retrieval-time entry point
│   ├── agent-wiki-ingest/                end-to-end orchestrator (all of the above)
│   └── scripts/build_agent_wiki.py       deterministic builder (render-*/catalog)
├── docs/
│   ├── design.md      design & rationale
│   └── schema.md      on-disk page/index schema
├── experiments/       the empirical evidence (see RESULTS-SUMMARY.md)
│   ├── RESULTS-SUMMARY.md
│   ├── twobatch-*.md  the comparison reports (wiki vs no-wiki; skills vs guidelines; …)
│   ├── pruned-index-hypothesis.md
│   ├── metrics/       per-trial metric rollups (.jsonl)
│   └── harness/       sandbox runner + comparison scripts to reproduce
└── wikis/             worked examples — wikis built by the skills above
    ├── wiki-twobatch/            16-task corpus, guidelines arm
    ├── wiki-twobatch-skills/     same corpus, skills-only arm
    ├── wiki-twobatch-both/       skills + guidelines
    └── wiki-twobatch-pruned/     skills + only no-skill-coverage atomics (delete-on-promote)
```

## Reading order

1. **`docs/design.md`** — what the wiki is and why it's shaped this way.
2. **`experiments/RESULTS-SUMMARY.md`** — the running tape of findings
   (wiki cuts cost ~20% at equal accuracy; skills beat guidelines; pointer
   wording is load-bearing; composition matters more than wiki size).
3. **`wikis/wiki-twobatch-skills/`** — open `AGENTS.md`, then `_index.jsonl`,
   then any page, to see a real built wiki end-to-end.
4. **`skills/agent-wiki-ingest/SKILL.md`** — how a batch of traces becomes a
   wiki in one pass.

## Scope of this exploration

These are **benchmark-derived** example wikis (a synthetic 16-task
file-format corpus). The raw per-trial sandbox transcripts and any wikis built from
internal trajectory corpora are intentionally **not** included — only the metric
rollups, the narrative reports, and the benchmark-derived wikis. Source links in
wiki frontmatter are shown in the generic form `trajectories/<session-id>.json`.

The skills here are a **standalone reference copy**, runnable via
`skills/scripts/build_agent_wiki.py`; they are not wired into any plugin loader
in this tree.
