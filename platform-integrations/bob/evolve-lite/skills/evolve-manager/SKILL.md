---
name: evolve-manager:merge-forks
description: Discover GitHub forks of the main evolve repo, merge their entity libraries with versioning-aware conflict resolution, test for regressions on main-repo skills only, and deduplicate the combined library.
---

# Merge Forks into Main Repo

## Overview

`evolve-manager:merge-forks` orchestrates a safe, regression-protected merge of evolve entity
libraries from GitHub forks into the local main repo. It runs the full pipeline:

```
discover forks
  → [0] PR gate: skip forks without an open PR against the main repo
    → clone + stage fork entities (accepted forks only)
      → snapshot main-repo entity manifest
        → versioning-aware merge
          → [A] quality gate on merged test fixtures
            → [B] rubric tests (main-repo entities only) → baseline_rate
              → [C] full skill dedup (both phases)
                → [D] rubric tests (main-repo entities only) → post_rate
                  → [E] threshold gate
                        PASS → commit merged entities to .evolve/entities/
                        FAIL → show diff, pause for user decision
  → write merge_report.json (always, even on error/skip)
```

Fork-sourced entities are **never** counted against the regression threshold. Only entities
that existed in the main repo before the merge are subject to the threshold gate.

## Usage

### Basic (agent handles fork discovery and cloning)

The PR gate is **on by default**. The upstream repo is auto-detected from the local git
`origin` remote. Set `GITHUB_TOKEN` (or pass `--github-token`) for private repos or higher
API rate limits.

```bash
python3 .bob/skills/evolve-manager/scripts/merge_forks.py \
  --fork-dirs .evolve/tmp/fork-staging/fork-a .evolve/tmp/fork-staging/fork-b
```

### With explicit main repo (required if git remote is not GitHub)

```bash
python3 .bob/skills/evolve-manager/scripts/merge_forks.py \
  --fork-dirs .evolve/tmp/fork-staging/fork-a \
  --main-repo my-org/my-evolve-repo
```

### Disable PR gate (merge any fork regardless of PR status)

```bash
python3 .bob/skills/evolve-manager/scripts/merge_forks.py \
  --fork-dirs .evolve/tmp/fork-staging/fork-a \
  --no-require-pr
```

### With relaxed threshold (allow up to 20% regression)

```bash
python3 .bob/skills/evolve-manager/scripts/merge_forks.py \
  --fork-dirs .evolve/tmp/fork-staging/fork-a \
  --threshold 0.8
```

### With custom version-diff sensitivity

```bash
python3 .bob/skills/evolve-manager/scripts/merge_forks.py \
  --fork-dirs .evolve/tmp/fork-staging/fork-a \
  --version-diff-threshold 0.3
```
Lower values preserve more dual-section history. Higher values replace more aggressively.

### Dry run (no writes)

```bash
python3 .bob/skills/evolve-manager/scripts/merge_forks.py \
  --fork-dirs .evolve/tmp/fork-staging/fork-a \
  --dry-run
```

## Flags

| Flag | Default | Description |
|---|---|---|
| `--fork-dirs` | (required) | Space-separated list of pre-cloned fork directories |
| `--threshold` | `1.0` | Min rubric pass rate for main-repo tests (1.0 = no regressions) |
| `--version-diff-threshold` | `0.5` | Jaccard similarity below which dual-section merging is used |
| `--dry-run` | off | Show all decisions without writing any entity files (report still written) |
| `--force-commit` | off | Skip the threshold gate and commit the merge regardless |
| `--report-dir` | `.evolve/tests/dedup/` | Directory for dedup JSON reports (including `merge_report.json`) |
| `--main-repo` | auto | Upstream GitHub repo as `owner/repo` (auto-detected from git `origin` remote) |
| `--require-pr` | on | Skip forks without an open PR against `--main-repo` |
| `--no-require-pr` | — | Disable the PR gate — merge all provided forks |
| `--github-token` | env | GitHub token for API calls (falls back to `GITHUB_TOKEN` env var) |

## Exit Codes

| Code | Meaning | Action |
|---|---|---|
| `0` | Merge succeeded | Entities are live in `.evolve/entities/` |
| `1` | Hard failure | Fix the reported error before re-running |
| `2` | Threshold breach | Main-repo test pass rate dropped — user must decide keep or roll back |

## Versioning-Aware Merge

When the same entity slug exists in both the main repo and a fork, the script compares
them using token-set Jaccard similarity on `trigger + content`:

- **Jaccard >= `--version-diff-threshold`** (default 0.5): minor update — fork content
  replaces main-repo content outright
- **Jaccard < `--version-diff-threshold`**: significant divergence — a dual-section entity
  is written with:
  - `## Current Version` — fork content (newer information, higher version)
  - `## Previous Version` — original main-repo content (used as baseline for rubric tests)

The entity's `version` frontmatter field is set to the fork's version. A `base_version` field
records the original main-repo version for traceability.

## Regression Gate

The threshold gate applies only to entities listed in
`.evolve/tmp/merge-workspace/main_entity_slugs.json` (snapshotted before any fork is merged).

For dual-section entities, the rubric test runs against the full merged file (both sections).
The `must_include` terms come from the **original main-repo rubric**, not the fork's. This means
the test checks whether the merged skill still satisfies what the main-repo version promised.

## Reports

| File | Contents |
|---|---|
| `.evolve/tests/dedup/merge_report.json` | **Full merge run summary** — PR gate results, accepted/skipped forks, merge decisions, pass rates, outcome |
| `.evolve/tests/dedup/quality_gate_report.json` | Phase 1 format/recall/eval results |
| `.evolve/tests/dedup/refine_report.json` | Phase 2 cluster decisions (merge/discard/keep) |
| `.evolve/tests/evaluation/report.json` | Pre-dedup rubric test results (baseline) |
| `.evolve/tests/evaluation/report_post.json` | Post-dedup rubric test results |
| `.evolve/tmp/merge-workspace/main_entity_slugs.json` | Entity provenance manifest |

### `merge_report.json` schema

```json
{
  "timestamp": "<ISO-8601 UTC>",
  "dry_run": false,
  "outcome": "success | skipped | dry-run | threshold-breach | error",
  "outcome_reason": "<human-readable explanation>",
  "threshold": 1.0,
  "baseline_pass_rate": 0.95,
  "post_dedup_pass_rate": 0.95,
  "forks": {
    "accepted": ["path/to/fork-a"],
    "skipped": ["path/to/fork-b"],
    "pr_check_details": [
      {
        "fork_dir": "path/to/fork-a",
        "has_pr": true,
        "pr_number": 42,
        "pr_title": "Add new skills",
        "fork_owner": "alice",
        "pr_head": "alice:main",
        "reason": "Open PR #42: 'Add new skills'"
      }
    ]
  },
  "merge_decisions": {
    "summary": { "keep-main": 10, "fork-replaces-main": 2, "dual-section": 1 },
    "details": [{ "slug": "...", "action": "...", "fork": "...", "jaccard": 0.7 }]
  }
}
```

## Rollback

A backup of the pre-merge `.evolve/entities/` is always written before any live file is
touched (even on `--dry-run` the backup is skipped):

```bash
# Roll back to pre-merge state
rm -rf .evolve/entities/
cp -r .evolve/tmp/pre-merge-backup/ .evolve/entities/
```

## Supporting Files

| File | Purpose |
|---|---|
| `scripts/merge_forks.py` | Main orchestration script |
