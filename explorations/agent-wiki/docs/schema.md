# Agent-wiki: on-disk schema reference

The precise file format of an agent-wiki — directory layout, every page
kind, the load-bearing metadata fields, how pages link, and the lifecycle by
which atomic guidelines get promoted into clusters or archived under skills.

For the *why* behind this structure, see
[`design.md`](design.md). For the recall-time contract
an agent follows, see
[`_default_agents.md`](../skills/scripts/_default_agents.md)
(copied into each wiki as `AGENTS.md`). The source of truth for everything
below is the builder
[`build_agent_wiki.py`](../skills/scripts/build_agent_wiki.py);
real examples are drawn from the `wiki-twobatch-*` example wikis.

---

## 1. Directory layout

```
<wiki-root>/
├── AGENTS.md            ← recall contract (bootstrapped from the template)
├── index.md             ← human-friendly overview (catalog-generated)
├── _config.yaml         ← durable taxonomy: tags, clusters, tasks, overrides
├── _index.jsonl         ← agent retrieval index (one row per page)
├── _audit.log           ← append-only JSONL log of mutations + recall events
├── _archived/           ← guidelines retired by delete-on-promote
│   └── <slug>__<gid>.md
├── summaries/
│   ├── <session_id>.md              ← one episodic summary per session
│   ├── <session_id>__<arc>.md       ← arc-split summary (long sessions)
│   └── index.md
├── guidelines/
│   ├── <slug>__<gid>.md             ← atomic guideline (one rule)
│   ├── <slug>__cluster.md           ← themed aggregator (recall-preferred)
│   ├── _id_index.json               ← guideline id → relpath
│   └── index.md
├── skills/
│   ├── <slug>/SKILL.md              ← callable workflow page
│   ├── <slug>/scripts/<file>        ← optional sibling scripts (Bash-runnable)
│   ├── _id_index.json               ← skill slug → relpath
│   └── index.md
└── tasks/
    ├── <slug>__task.md              ← cross-session comparison
    ├── <slug>__subtask.md           ← per-session workstream
    └── index.md
```

**Filename suffixes are the navigation contract.** A page's role is decided
by its suffix, and the tooling relies on it — do not rename:

| Pattern | Role |
|---|---|
| `<slug>__<gid>.md` (in `guidelines/`) | atomic guideline; `<gid>` = the `id:` |
| `<slug>__cluster.md` | cluster aggregator |
| `<session_id>.md` / `<session_id>__<arc>.md` | summary (single / arc-split) |
| `<slug>__task.md` | cross-session task comparison |
| `<slug>__subtask.md` | per-session workstream |
| `<slug>/SKILL.md` | skill |

Files prefixed `_` (`_index.jsonl`, `_config.yaml`, `_audit.log`,
`_id_index.json`, `_archived/`) are machinery, not content pages.

---

## 2. Page kinds and their frontmatter

Each page is markdown with YAML frontmatter. Fields are either **authored at
render-time** (written once by the `render-*` pass, stable thereafter) or
**catalog-managed** (recomputed and force-overwritten on every `catalog`
run). The split matters: never hand-edit a catalog-managed field — it'll be
clobbered next catalog.

### Summary — `summaries/<session_id>.md`

`type: episodic-summary`. The provenance anchor every other page links back
to. One per session (or per arc for long, split sessions).

| Field | Origin | Meaning |
|---|---|---|
| `session_id`, `agent`, `model`, `goal`, `outcome` | render | session identity + one-line goal + success/partial/failure |
| `duration_seconds`, `tools_used`, `sources` | render | wall-clock, tool names, provenance paths (normalized JSON + raw transcript) |
| `recalled_guidelines` | render | guidelines the session saw, each `{id, title, status, evidence?}` |
| `arc`, `sibling_summaries` | render | only on arc-split sessions |
| `tags`, `tool_calls`, `errors`, `dead_end_paths`, `wiki_consulted` | **catalog** | computed from the normalized trajectory |
| `contributed_guidelines`, `contributed_skills` | **catalog** | reverse links — pages this session produced |
| `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, `total_cost_usd` | **catalog** | token + cost metrics (omitted when zero) |
| `verified_at` | **catalog** | date of last catalog run |

```yaml
---
type: episodic-summary
session_id: <uuid>
agent: bob
model: premium
goal: One sentence describing what the user asked for.
outcome: success
duration_seconds: 40.3
tools_used: [execute_command, attempt_completion]
sources:
  - trajectories/<sid>-openai-chat-completions.analysis.json
  - /path/to/raw/session.json
# ── below: catalog-managed ──
tags: []
tool_calls: 7
errors: 0
wiki_consulted: false
contributed_guidelines: [<gid>, ...]
contributed_skills: [<slug>, ...]
total_cost_usd: 0.18
verified_at: 2026-06-09
---
```

### Atomic guideline — `guidelines/<slug>__<gid>.md`

`type: guideline` (also `workflow` / `script` / `command-template`). One
reusable rule. `<gid>` is a 12-hex content hash and equals the `id:`.

| Field | Origin | Meaning |
|---|---|---|
| `id`, `type` | render | content-hash id; page kind |
| `trigger` | render | situational context when the rule applies |
| `agent` | render | source agent (`bob`, `claude-code`, …); defaults to `claude-code` |
| `tags` | render, then **catalog** | topical tags; catalog re-syncs from `_config.yaml` |
| `sources`, `related_summary` | render | provenance: normalized JSON path + the summary page |
| `cluster`, `superseded_by` | **catalog** | set when this atomic is a cluster member |
| `verified_at` | **catalog** | date of last catalog run |

The body carries the rule prose, an optional `## Rationale`, a
`## Sources` footer, and a catalog-injected `## Used by` section listing
sessions that recalled it.

```yaml
---
id: 84ed6cf26387
type: guideline
trigger: Need to put a multi-line script inside a running Docker container before executing it.
agent: claude-code
tags: [docker, heredoc, shell, scripting, example]
sources:
  - trajectories/df2b08e4-openai-chat-completions.analysis.json
related_summary: summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md
verified_at: 2026-06-09
cluster: container-boundary-one-shot__cluster.md       # ← stamped by catalog
superseded_by: container-boundary-one-shot__cluster.md  # ← stamped by catalog
---
```

### Cluster — `guidelines/<slug>__cluster.md`

`type: cluster`, `id: cluster:<slug>`. A themed aggregator over ≥2 atomic
guidelines that share a rule. **Regenerated whole on every catalog run** from
the membership declared in `_config.yaml`; always `priority: high`.

```yaml
---
type: cluster
slug: container-boundary-one-shot
title: Cross the host/container boundary in one docker exec
tags: [docker, container, shell, io]
verified_at: 2026-06-09
members:
  - id: 84ed6cf26387
    link: heredoc-python-scripts-into-the__84ed6cf26387.md
  - id: 6c2bd298dd0d
    link: read-in-container-files-via-docker-exec__6c2bd298dd0d.md
priority: high
---
```

Body: description, optional `## Takeaway` (the actionable one-line rule), and
a `## Members` table. Members keep their own pages and provenance — the
cluster aggregates, it doesn't absorb.

### Skill — `skills/<slug>/SKILL.md`

`type: skill`, `id: skill:<slug>`. A callable workflow page. Authored once by
`render-skill`; **not touched by catalog**.

| Field | Meaning |
|---|---|
| `name`, `description`, `trigger` | slug, one-paragraph summary, when-to-use |
| `agent`, `sources`, `related_summary` | source agent + provenance |
| `tags`, `verified_at` | topical tags; render date |

Body: `## Overview`, optional `## When To Use`, `## Workflow`, `## Sources`.
Optional sibling scripts live under `skills/<slug>/scripts/` (shell scripts
are written `chmod 755`).

```yaml
---
id: skill:transform-json-with-jq-and-persist-filter-args-yaml
type: skill
name: transform-json-with-jq-and-persist-filter-args-yaml
description: Use a single jq pipeline to filter, reshape, and sort JSON to a target schema …
trigger: "A task gives an input JSON and asks for a transformed output plus a YAML of the jq filter + args …"
agent: bob
sources:
  - trajectories/d0e03862-openai-chat-completions.analysis.json
related_summary: summaries/d0e03862-30c5-49b6-9aef-b97dcea57dc0.md
verified_at: 2026-06-09
tags: [jq, json, yaml, example]
---
```

### Task / subtask — `tasks/<slug>__task.md`, `tasks/<slug>__subtask.md`

`task-comparison` pages (`id: task:<slug>`) are cross-session comparison
tables, **regenerated each catalog run** from `_config.yaml`'s `tasks.<slug>`
definition + the sessions it classifies. `subtask` pages (`id:
subtask:<slug>`) are per-session workstream narratives, **authored standalone**
and not regenerated. Both carry `type`, `slug`, `title`, `tags`,
`verified_at`; tasks add `sessions:` (row count), subtasks add
`parent_session_id` / `parent_summary`.

### id conventions

- **Atomic guidelines**: a 12-hex content hash (e.g. `84ed6cf26387`); the
  filename suffix matches, so id ↔ file round-trips.
- **Everything else**: a kind-prefixed slug — `cluster:<slug>`,
  `skill:<slug>`, `task:<slug>`, `subtask:<slug>`.

---

## 3. Index, config, and audit files

### `_index.jsonl` — the retrieval index

One JSON object per line, one line per cluster / skill / guideline / task /
subtask page. This is what an agent reads at recall-time. Rows are sorted
**clusters → skills → guidelines → tasks → subtasks**, so the most
consolidated and directly-actionable artifacts come first. Common keys:
`kind`, `id`, `title`, `tags`, `trigger`, `summary` (≤240-char snippet),
`link`. Per-kind extras: clusters add `members` + `priority: high`; skills
add `priority: high`; guideline rows add `cluster` and (when clustered)
`superseded_by`; task rows add `family`; subtask rows add
`parent_session_id` / `parent_summary`.

```jsonl
{"kind": "cluster", "id": "cluster:container-boundary-one-shot", "title": "Cross the host/container boundary in one docker exec", "tags": ["docker","container","shell","io"], "trigger": "", "summary": "Benchmark tasks frequently live inside a named Docker container…", "link": "guidelines/container-boundary-one-shot__cluster.md", "members": ["84ed6cf26387","6c2bd298dd0d"], "priority": "high"}
{"kind": "skill", "id": "skill:aggregate-jsonl-records-top-n-by-sum-and-count", "title": "aggregate-jsonl-records-top-n-by-sum-and-count", "tags": ["jsonl","python","aggregation","example"], "trigger": "Task gives a directory of large JSONL files…", "summary": "Aggregate many JSONL files in one streaming Python pass…", "link": "skills/aggregate-jsonl-records-top-n-by-sum-and-count/SKILL.md", "priority": "high"}
{"kind": "guideline", "id": "3c019235c9f8", "title": "Format ISO 8601 to YYYY-MM-DD with split T", "tags": ["jq","iso-8601","date-formatting","example"], "trigger": "Inside a jq filter, you need only the calendar date…", "summary": "…use `(.last_login | split(\"T\")[0])`.", "link": "guidelines/format-iso-8601-to-yyyy-mm-dd-with__3c019235c9f8.md", "cluster": null}
```

**Archived guidelines are absent from `_index.jsonl`** — that's what makes
archiving remove a page from recall.

### `_config.yaml` — the durable taxonomy

The one authored file that survives catalog regeneration. Structure:

```yaml
schema_version: 1
tags:
  guideline:
    <gid>: [tag, tag, ...]      # guideline id → tags (drives "By tag" + clustering)
clusters:
  <slug>:
    title: <string>
    description: <string>
    takeaway: <string>
    members: [<gid>, ...]       # the cluster's atomic members
    tags: [tag, ...]
tasks:
  <slug>:
    title: <string>
    family: <string>
    family_match: { goal_substring: [<substr>, ...] }
    intro: <string>
    findings: <string>
    tags: [tag, ...]
session_family_overrides:
  <session_id>: { family: <str|null>, trial: <int|null>, condition: <str|null> }
```

`tags.guideline` and `clusters` are written by `render-guidelines` /
`render-cluster`; `catalog` reads them back to stamp atomic frontmatter and
regenerate cluster pages. `tasks` + `session_family_overrides` drive
task-comparison classification.

### `_id_index.json` — id → path

A flat map in both `guidelines/` and `skills/`, used to resolve backlinks
(e.g. a summary's `contributed_guidelines` ids → file paths). Archiving an
atomic **pops** its entry here (see §5).

```json
{ "84ed6cf26387": "guidelines/heredoc-python-scripts-into-the__84ed6cf26387.md" }
```

### `_audit.log` — append-only mutation + recall log

One JSON line per event. Three action types:

```jsonl
{"action": "summary.guideline_use", "session_id": "<uuid>", "id": "<gid>", "status": "followed", "ts": "…Z"}
{"action": "synthesize_skill", "session_id": "<uuid>", "skill_name": "<slug>", "scripts": ["run.sh"], "ts": "…Z"}
{"action": "archive_guideline", "id": "<gid>", "reason": "covered_by_skill", "target": "<slug>", "src": "guidelines/…md", "dst": "_archived/…md", "ts": "…Z"}
```

`reason` is `covered_by_skill` or `covered_by_cluster`. The audit log is the
durable record of promotions/archivals even though archived pages leave the
index.

---

## 4. How files link to each other

Forward links are **authored at render-time**; reverse links are
**recomputed by catalog** from the forward ones. Forward is the source of
truth.

```
            ┌──────────────────────────── provenance (forward) ───────────────────────────┐
            ▼                                                                               │
 guidelines/<slug>__<gid>.md ──related_summary:──▶ summaries/<sid>.md ──sources:──▶ normalized JSON ──▶ raw transcript
            ▲                                              │
            │   contributed_guidelines: / contributed_skills:  (reverse — catalog inverts related_summary)
            └──────────────────────────────────────────────┘

 guidelines/<slug>__<gid>.md ──cluster: / superseded_by:──▶ guidelines/<slug>__cluster.md
            ▲                                                        │
            └────────────────────── members: ───────────────────────┘   (bidirectional)

 _id_index.json :  <gid> ──▶ relpath          _index.jsonl :  row.link ──▶ page file
```

- A **guideline → summary → trajectory** chain makes every rule auditable.
- `catalog` builds **`contributed_guidelines` / `contributed_skills`** on the
  summary by inverting all guideline/skill `related_summary:` fields — so the
  summary knows what it produced without that being hand-maintained.
- **Cluster ↔ member** is bidirectional: the cluster lists `members:`; each
  member is stamped `cluster:` + `superseded_by:`.

---

## 5. Lifecycle: promotion & archival

```
                         render-guidelines
                                │
                                ▼
                   ┌──────────────────────────┐
                   │         ATOMIC           │
                   │ guidelines/<slug>__<gid> │
                   │ in _id_index.json        │
                   │ in _index.jsonl          │
                   └──────────────────────────┘
                         │                   │
        render-cluster   │                   │  render-skill --archive-covered
        (+ catalog)      │                   │  — or — render-cluster --archive-members
                         ▼                   ▼
        ┌────────────────────────┐   ┌──────────────────────────┐
        │       CLUSTERED        │   │        ARCHIVED          │
        │ file STAYS in place    │   │ file MOVES → _archived/  │
        │ +cluster: +superseded… │   │ popped from _id_index    │
        │ still in both indexes  │   │ ABSENT from _index.jsonl │
        │ cluster row priority:hi│   │ audit: archive_guideline │
        └────────────────────────┘   │ (unreachable at recall)  │
                                      └──────────────────────────┘
```

### ATOMIC → CLUSTERED

Authored by declaring the cluster (`render-cluster` writes
`_config.yaml/clusters.<slug>` + the `__cluster.md` page). On the next
`catalog`, each member atomic is **stamped** `cluster:` and `superseded_by:`
in its frontmatter. The member **file stays in place**, stays in
`_id_index.json`, and stays in `_index.jsonl` (now carrying `superseded_by`).
The cluster gets its own `_index.jsonl` row with `priority: high`. At recall
the cluster is preferred; members remain reachable for their original wording
+ provenance.

### ATOMIC → ARCHIVED (delete-on-promote)

When a skill (or cluster) subsumes an atomic, the atomic is **soft-archived**:

1. file moved `guidelines/<slug>__<gid>.md` → `_archived/<slug>__<gid>.md`
2. its entry is **popped** from `guidelines/_id_index.json`
3. an `archive_guideline` line is appended to `_audit.log`
4. on the next catalog it is **not scanned** (it's outside `guidelines/`), so
   it disappears from `_index.jsonl` — **unreachable at recall**, still on
   disk for audit. Reversal is manual.

Two triggers:

| Trigger | Flag | Audit `reason` |
|---|---|---|
| Cluster created | `render-cluster --archive-members` | `covered_by_cluster` |
| Skill synthesized | `render-skill --archive-covered` | `covered_by_skill` |

### Coverage inference (`--archive-covered`)

A skill archives an atomic only if `_skill_covers_atomic` returns true via
**any** of three conservative paths (biased toward false-negatives — when in
doubt, the atomic survives):

1. **Tag-superset** — the atomic's tags ⊆ the skill's tags **and** their
   intersection has ≥2 tags outside a `_GENERIC_TAGS` stop-set
   (`stdlib`, `parsing`, `agent-behavior`, `binary`, `headers`, …).
2. **Slug-keyword** — a ≥4-char, non-stopword token from the skill slug
   appears in the atomic's title.
3. **Format-identifier** — an uppercase (`PNG`, `ZIP`) or CamelCase (`WebP`)
   token in the skill description appears in the atomic's title. Catches
   family-broad skills whose slug abstracts the format names away.

### What catalog recomputes vs. what's authored once

| Recomputed every `catalog` (force-replaced) | Authored once at render |
|---|---|
| guideline: `verified_at`, `tags`, `cluster`, `superseded_by`; `## Used by` | guideline: `id`, `type`, `agent`, `trigger`, `sources`, `related_summary`, body |
| summary: `tags`, `tool_calls`, `errors`, `dead_end_paths`, `wiki_consulted`, `contributed_guidelines`, `contributed_skills`, token metrics, `verified_at` | summary: `session_id`, `agent`, `model`, `goal`, `outcome`, `sources`, narrative |
| cluster + task pages (regenerated whole); all `index.md`; `_index.jsonl`; priority tiers | cluster/task definitions in `_config.yaml`; skill pages; subtask pages |

Archiving is one-way; reversing it means moving the file back and
re-cataloging by hand.

---

## 6. Worked example — one real chain

Tracing the atomic `heredoc-python-scripts-into-the__84ed6cf26387` through one of the example wikis.

**(a) The atomic** carries forward links to its summary + its cluster (the
`cluster:`/`superseded_by:` pair was stamped by catalog when the cluster was
declared):

```yaml
id: 84ed6cf26387
type: guideline
agent: claude-code
tags: [docker, heredoc, shell, scripting, example]
sources:
  - trajectories/df2b08e4-openai-chat-completions.analysis.json
related_summary: summaries/df2b08e4-7853-47ec-9c46-fee4b0a33eb7.md
cluster: container-boundary-one-shot__cluster.md
superseded_by: container-boundary-one-shot__cluster.md
```

**(b) Follow `related_summary:`** to the summary — which closes the reverse
loop via the catalog-computed `contributed_guidelines` (and names the raw
transcript under `sources:`):

```yaml
type: episodic-summary
session_id: df2b08e4-7853-47ec-9c46-fee4b0a33eb7
agent: bob
goal: Aggregate JSONL records in a Docker container to produce /app/aggregates.json …
sources:
  - trajectories/df2b08e4-openai-chat-completions.analysis.json
  - /Users/…/.bob/tmp/…/chats/session-2026-06-09T07-11-df2b08e4.json   # raw trace
contributed_guidelines: [84ed6cf26387]                                  # ← reverse edge
contributed_skills: [aggregate-jsonl-records-top-n-by-sum-and-count]
```

**(c) Follow `cluster:`** forward to the aggregator, which lists the atomic
as a member — the bidirectional cluster↔member link:

```yaml
type: cluster
slug: container-boundary-one-shot
title: Cross the host/container boundary in one docker exec
members:
  - id: 84ed6cf26387
    link: heredoc-python-scripts-into-the__84ed6cf26387.md
  - id: 6c2bd298dd0d
    link: read-in-container-files-via-docker-exec__6c2bd298dd0d.md
priority: high
```

One atomic, four hops: **rule → summary → raw trajectory** (provenance), and
**rule ↔ cluster** (consolidation), with the summary's
`contributed_guidelines` closing the loop back to the rule. Every edge is
either authored at render (forward) or recomputed by catalog (reverse).

---

## See also

- [`design.md`](design.md) — why the wiki is shaped this way (rationale, principles, empirical results).
- [`_default_agents.md`](../skills/scripts/_default_agents.md) — the recall-time contract (`AGENTS.md`).
- [`build_agent_wiki.py`](../skills/scripts/build_agent_wiki.py) — the builder; the implementation of everything above.
