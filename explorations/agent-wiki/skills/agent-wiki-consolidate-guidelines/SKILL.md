---
name: agent-wiki-consolidate-guidelines
description: Read all atomic guidelines in wiki-twobatch/guidelines/ and propose themed clusters that group near-duplicates. Writes cluster pages and updates _config.yaml; originals are preserved with a `superseded_by:` backref.
---

# Agent Wiki — Consolidate Guidelines

## Overview

Spot duplicates and recurring themes across the corpus of atomic
guidelines. Author cluster pages that aggregate related variants and
record the membership in `_config.yaml`. **Originals stay** — clusters
reference them; nothing is moved or merged.

This is the cross-trajectory **pattern-recognition** pass of the
`agent-wiki` family. Run it after one or more `extract-guidelines`
sessions when the wiki has accumulated enough atomic guidelines that a
theme is visible.

## When to run

- After a batch of `extract-guidelines` runs, when you suspect duplicates.
- When `guidelines/index.md`'s "By tag" section has 3+ entries under the
  same tag and you want a canonical aggregator page for that theme.
- When users complain that recall returns N near-identical hits.

## Workflow

### Step 1: Read the corpus

```bash
uv run python plugin-source/skills/agent-wiki/scripts/build_agent_wiki.py dump-guidelines > /tmp/guidelines.json
```

Output is a JSON array of `{id, filename, title, trigger, cluster,
is_cluster_page, content}` for every page in `guidelines/`. The
`is_cluster_page` flag tells you which entries are existing aggregators
(`__cluster.md` suffix) — you will be **adding** new clusters, not
re-deriving existing ones.

Read the file:

```
Read /tmp/guidelines.json
```

### Step 2: Decide groupings

For each candidate cluster:

- **Theme**: a one-line statement of the shared idea ("when system EXIF tools are missing, parse JPEG bytes directly with stdlib").
- **Members**: 2–6 atomic guideline ids that share that theme. Atomic only
  — never include `is_cluster_page: true` entries.
- **Tags**: 2–4 short tags that describe the theme.

Rules:

1. **Don't cluster unrelated guidelines just to clean up the listing.** A cluster needs a real shared rule, not a shared topic.
2. **Don't merge content across atomic pages.** Each atomic page stays whole. The cluster's body summarizes the *theme* and links to members.
3. **Don't propose a cluster for a single guideline.** Wait for ≥2 members.
4. **Don't re-author an existing cluster** unless members materially changed. Skip clusters that already exist with the same membership (`existing_clusters` field below).

### Step 3: For each new cluster, output JSON

```json
{
  "slug": "exif-stdlib-fallback",
  "title": "EXIF stdlib parser fallback",
  "description": "1-2 paragraphs framing the shared theme.",
  "takeaway": "1 paragraph: the actionable rule the cluster captures.",
  "members": ["04474b0794e6", "de04f5adde2e", "4746bf445108"],
  "tags": ["exif", "stdlib", "fallback"]
}
```

Pipe to:

```bash
echo '<json>' | uv run python plugin-source/skills/agent-wiki/scripts/build_agent_wiki.py render-cluster
```

The helper:

- Updates `wiki-twobatch/_config.yaml` `clusters.<slug>` entry.
- Writes `guidelines/<slug>__cluster.md` with `priority: high`, member links, snippets pulled from disk.

### Step 4: Refresh indexes

After writing all new cluster pages:

```bash
uv run python plugin-source/skills/agent-wiki/scripts/build_agent_wiki.py catalog
```

`catalog` propagates the cluster membership back to atomic pages: each
member gets `cluster: <slug>__cluster.md` and `superseded_by:
<slug>__cluster.md` in its frontmatter, and the cluster page is
re-rendered against current member content.

## Best practices

1. **Write the takeaway first.** If you can't articulate one shared rule in a sentence, the cluster doesn't exist.
2. **Be conservative.** Two false-positive clusters cost more than two un-clustered duplicates.
3. **Preserve atomic provenance.** A reader should be able to navigate cluster → member → source trajectory in two clicks.
4. **Don't re-cluster within an existing cluster.** Sub-themes don't justify nesting.
5. Always tail-call `catalog` after the cluster loop.
