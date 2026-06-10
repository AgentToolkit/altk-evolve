---
type: wiki-index
verified_at: 2026-06-10
---

# wiki-twobatch-pruned

An evidence-grounded wiki of agent trajectories: each lesson links back to the trajectory that produced it. Built by the `agent-wiki` skill family from normalized agent transcripts.

## Sections

- [Tasks](tasks/index.md) — `__task.md` cross-session comparisons (0) + `__subtask.md` per-session workstreams (0)
- [Guidelines](guidelines/index.md) — atomic lessons + cluster aggregator pages (suffix `__cluster.md`); cluster pages are recall-preferred (9 atomic + 0 clusters)
- [Summaries](summaries/index.md) — episodic summaries (47 pages). Long sessions may be split into multiple arc-summaries that share a `session_id`.

## How content relates

```
raw .jsonl  ──normalize──▶  normalized JSON  ──summarize──▶  summary
                                                                │
                                                                └──▶  guideline (one or more)  ──cluster──▶  guideline (cluster) page
                                                                                                              │
                            task comparison page  ◀───────────────────────────────────────────────────────────┘
```

Provenance closes via:

- `summary.contributed_guidelines: [id, …]` (outbound)
- `guideline.related_summary: summaries/<sid>.md` (inbound)
- `guideline.cluster: <slug>__cluster.md` (themed group)
- `cluster.members[].link: <member>.md` (preserves originals)
- `_index.jsonl` at the wiki root for cheap filter+score retrieval

## For agents (recall-time)

Read [_index.jsonl](_index.jsonl) — one row per guideline + cluster page with `{id, kind, title, tags, trigger, summary, link}`. Filter by tag, score on trigger overlap, then follow `link` for the full content.

## Cluster pages

Cluster pages live in `guidelines/` with the `__cluster.md` suffix. They are themed aggregators that reference atomic-guideline siblings — the originals stay intact. At recall time clusters are preferred over their members; atomic members carry a `superseded_by:` field.

## Staleness

All pages stamp `verified_at`. Today: **2026-06-10**. Pages without an `expires_at` are valid until a follow-up trajectory contradicts them.
