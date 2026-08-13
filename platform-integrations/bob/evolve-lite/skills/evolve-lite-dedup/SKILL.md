---
name: evolve-lite:dedup
description: Two-phase skill library deduplication — runs all quality tests then merges or discards similar/duplicate skills. Use when the skill library may have grown stale, redundant, or inconsistent.
---

# Skill Library Deduplication

## Overview

`evolve-lite:dedup` cleans the skill library in two sequential phases.
Phase 2 will not run unless Phase 1 passes completely.

```
Phase 1 — Quality Gate     (quality_gate.py)
  ├─ Format check           required frontmatter, valid type, non-empty content
  ├─ Recall test            skill ranks ≤ 3 for its own trigger scenario
  ├─ Skill evaluation       content is self-consistent; skill-flow refs resolve
  ├─ Naming check           slug and trigger are well-formed
  └─ Version check          version field and changelog are consistent (warning only)

        ↓ only if all skills pass ↓

Phase 2 — Refine           (refine.py)
  ├─ Banality prune         remove entities too generic to provide recall value
  ├─ Similarity grouping    token-set Jaccard clustering
  ├─ keep-all               skills are distinct
  ├─ merge                  combine similar skills into one enriched entity
  └─ discard                remove near-identical duplicates, keep richest
```

## When To Use

- After several `evolve-lite:learn` runs, when the library may have grown.
- Before publishing or syncing skills to a shared repo.
- Any time recall results feel noisy or redundant.
- Periodically as a maintenance command (e.g. weekly).

## Usage

### Recommended workflow (with test regression protection)

Run this sequence instead of calling `dedup.py` directly. It captures a pass-count snapshot before dedup, runs dedup, then verifies no tests regressed.

```bash
# 1. Make sure fixtures and reports are up to date
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py --all
python3 .bob/skills/evolve-lite-test/scripts/check_tests.py --threshold 0.8

# 2. Snapshot the current pass counts
python3 .bob/skills/evolve-lite-test/scripts/snapshot_test_results.py \
    --out .evolve/tests/evaluation/pre_dedup_snapshot.json

# 3. Run dedup
python3 .bob/skills/evolve-lite-dedup/scripts/dedup.py

# 4. Re-run tests and compare against the snapshot
python3 .bob/skills/evolve-lite-test/scripts/check_tests.py --threshold 0.8
python3 .bob/skills/evolve-lite-test/scripts/snapshot_test_results.py \
    --compare .evolve/tests/evaluation/pre_dedup_snapshot.json \
    --out     .evolve/tests/evaluation/post_dedup_snapshot.json
```

**If step 4's comparison exits 1** (regression detected), see [Fixing regressions after dedup](#fixing-regressions-after-dedup) below.

### Full pipeline (automated, no regression protection)

```bash
python3 .bob/skills/evolve-lite-dedup/scripts/dedup.py
```

### Dry run — see decisions without changing anything

```bash
python3 .bob/skills/evolve-lite-dedup/scripts/dedup.py --dry-run
```

### Interactive — review each duplicate cluster manually

```bash
python3 .bob/skills/evolve-lite-dedup/scripts/dedup.py --interactive
```

### Quality gate only

```bash
python3 .bob/skills/evolve-lite-dedup/scripts/dedup.py --phase1-only
```

### With custom similarity threshold

```bash
python3 .bob/skills/evolve-lite-dedup/scripts/dedup.py --threshold 0.6
```

### Save reports to a specific directory

```bash
python3 .bob/skills/evolve-lite-dedup/scripts/dedup.py --report-dir .evolve/tests/dedup/
```

## Phase 1 — Quality Gate

Iterates every `.md` file under `.evolve/entities/` and runs three checks:

### 1. Format Check

Each entity must have:
- `type` field set to `guideline`, `atomic-skill`, or `skill-flow`
- `trigger` field with at least 10 characters
- `owner` and `visibility` fields
- Non-empty content body

Skill-flows must have a non-empty `atomic_skills` references field.

Optional body sections (`## Requirements`, `## Imports`, `## Dependencies`, `## Documentation`) are not required but are validated when present:
- Every package/tool in `## Requirements` must be mentioned in the content body
- Every module/symbol in `## Imports` must be mentioned in the content body

### 2. Recall Test

Generates a realistic user question from the entity's trigger (using the same
`trigger_to_realistic_question` logic as `evolve-lite-test`) and scores the
entity against the full recall manifest using the keyword-overlap heuristic.

**Pass condition**: the skill ranks #1 or appears in the top-3 with score > 0.

A skill that fails the recall test will not be retrieved when it should be —
this indicates a weak or ambiguous trigger that needs to be rewritten.

### 3. Skill Evaluation

Checks that the skill content is internally self-consistent:
- Derives `must_include` terms from the content (backtick commands, identifier
  terms, or key words).
- Verifies those terms appear in the content itself (alignment score ≥ 0.5).
- For skill-flows: checks that every slug listed in `atomic_skills` resolves
  to a real file under the entities directory.

### Quality Gate Exit Codes

| Code | Meaning |
|------|---------|
| `0`  | All skills pass — safe to proceed to Phase 2 |
| `1`  | One or more skills failed — Phase 2 blocked |

Fix failing skills before re-running dedup. See [Fixing gate failures](#fixing-gate-failures-dedup) below for per-failure-type guidance.

## Phase 2 — Refine

### Banality Prune (pre-clustering)

Before clustering, every `atomic-skill` and `guideline` entity is checked for banality. `skill-flow` entities are exempt because their composition order is non-obvious even when individual steps are simple.

An entity is considered banal and pruned when **any** of the following are true:

| Condition | Example |
|---|---|
| Content ≤ 30 chars with no backtick command | `"Activate the virtual environment."` |
| Content or trigger matches a common-sense pattern | `"install dependencies"`, `"git commit"`, `"run the application"` |
| Content is a near-verbatim restatement of the trigger (Jaccard ≥ 0.85) | Trigger: "When activating the venv" → Content: "Activate the venv." |

Pruned entities are deleted before clustering so they can never be merged into a richer skill.

Use `--no-prune` to skip this step and review entities manually.


Groups entities by **token-set Jaccard similarity** on the combined
`trigger + content` text. The default threshold is **0.45** — pairs scoring
at or above this value are grouped into a cluster.

### Automatic Decisions

| Jaccard range | Decision | Action |
|---|---|---|
| ≥ 0.75 | `discard` | Keep the most detailed skill, delete the others |
| ≥ 0.45 (threshold) | `merge` | Write a merged entity with combined trigger into the richest file, delete others |
| < 0.45 | `keep-all` | No action |

The "richest" skill in a cluster is the one with the longest content.

For `merge`, the combined trigger is all unique triggers joined with `; ` so
recall still fires on any of the original phrasings.

### Interactive Mode

With `--interactive`, each multi-skill cluster is printed with:
- Slug, trigger, and content preview for each member
- The automatic suggestion

You are prompted to choose:
- `k` keep-all
- `m` merge
- `d` discard
- `s` skip (leave for later)
- Enter to accept the suggestion

### Reports

Both phases write JSON reports (default: `.evolve/tests/dedup/`):

- `quality_gate_report.json` — per-skill format / recall / eval results
- `refine_report.json` — per-cluster decisions and removed/merged paths

## Fixing gate failures (dedup) {#fixing-gate-failures-dedup}

When Phase 1 blocks dedup, the `quality_gate_report.json` and the console output show per-skill results. Fix each `❌` entry using the pattern that matches.

---

**Format check failure** — required frontmatter field is missing or invalid

```
❌ my-skill   FAIL [format]  missing: owner, visibility
```

Open the entity file. Add the missing YAML frontmatter keys:
- `type:` — must be `guideline`, `atomic-skill`, or `skill-flow`
- `trigger:` — at least 10 characters
- `owner:` — your username (from `identity.user` in `evolve.config.yaml`)
- `visibility:` — set to `private`
- `atomic_skills:` — required for `skill-flow` type; list the slugs of component atomic skills

If `## Requirements` or `## Imports` sections are present, make sure every package/tool they list is also mentioned in the content body. Remove any that are not.

---

**Recall failure** — skill not surfacing in top-3 for its own trigger scenario

```
❌ my-skill   FAIL [recall]  rank=8  score=1  matched_terms=['cli']
```

The `trigger` field does not contain words a user would type when describing the problem. Rewrite the trigger:
- Use the symptom, error message, or task keywords — not the solution.
- Include the specific command, flag name, or error text that distinguishes this skill from others.
- Inspect the `top5` results from `run_recall_tests.py --verbose` to see which skills outranked it and what vocabulary they share. Add differentiating terms.

After rewriting the trigger, regenerate the pseudo-conversation fixture:
```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py <path-to-entity.md>
```
Then re-run `dedup.py --phase1-only` to verify the fix.

---

**Skill evaluation failure** — content is not self-consistent

```
❌ my-skill   FAIL [eval]  score=0.25  missed=['orchestrate agents import', '--kind']
```

`missed` shows command terms that should appear in the skill content but don't. For each missed term:
- Add the missing command, flag, or tool to the content body (preferred).
- If the term was mistakenly extracted, regenerate the fixture: `generate_skill_tests.py <path-to-entity.md>`

For `skill-flow` failures showing `unresolved atomic_skills: [slug1, slug2]` — the slugs in the `atomic_skills` frontmatter field do not correspond to real files. Either create those atomic skill files, or correct the slugs to match existing ones.

---

**Multiple failures on the same skill**

Fix in this order: format → content → recall. Format issues can mask the other checks, and fixing content often improves recall naturally (the distinguishing keywords appear in both).

---

## Fixing regressions after dedup {#fixing-regressions-after-dedup}

When `snapshot_test_results.py --compare` exits 1, one or more test fixtures reference a skill that was renamed, merged, or deleted during dedup.

```
❌ Recall test   REGRESSION  12→10 passed  (Δ-2)
```

**Step 1 — identify which fixtures broke**

Run the recall test in verbose mode to find the newly-failing skills:
```bash
python3 .bob/skills/evolve-lite-test/scripts/run_recall_tests.py --verbose
```

Look for `❌` entries that were `✅` before dedup. These fixtures reference slugs that no longer exist.

**Step 2 — trace the dedup action**

Open `.evolve/tests/dedup/refine_report.json`. Find the cluster that contained the now-missing slug. The report shows whether it was:
- **merged** into another skill (the surviving slug is `survivor`)
- **discarded** (the slug was deleted outright)

**Step 3 — update or regenerate the fixture**

- *Merged*: The surviving skill should cover the same scenario. Open the fixture file at `.evolve/tests/pseudo_conversations/<old-slug>.json` and update `skill_slug` to the survivor slug. Also update the path in the `conversation[system]` block if it references the old filename. Then verify:
  ```bash
  python3 .bob/skills/evolve-lite-test/scripts/run_recall_tests.py --verbose
  ```
- *Discarded*: The skill was intentionally removed. If the scenario it covered is no longer served by any skill, delete its fixture file — it is no longer a valid test. If the scenario *should* still be covered, the discard was wrong: restore the entity from git (`git checkout HEAD -- <entity-path>`) and re-run dedup with `--interactive` to keep that skill.

**Step 4 — confirm no regression**

```bash
python3 .bob/skills/evolve-lite-test/scripts/snapshot_test_results.py \
    --compare .evolve/tests/evaluation/pre_dedup_snapshot.json
```

This must exit 0 before the dedup run is considered complete.

## Supporting Scripts

| Script | Purpose |
|---|---|
| `scripts/quality_gate.py` | Phase 1 runner (can be run standalone) |
| `scripts/refine.py` | Phase 2 runner (can be run standalone) |
| `scripts/dedup.py` | Orchestrator — runs both phases in sequence |
| `evolve-lite-test/scripts/check_tests.py` | Content + recall gate with pass-rate threshold |
| `evolve-lite-test/scripts/snapshot_test_results.py` | Pre/post snapshot for regression detection |
