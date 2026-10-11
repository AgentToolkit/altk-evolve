# Evolve Bob — Skills & Modes Pipeline

A reference for the two modes, all skills, and how they connect into pipelines.

---

## All Skills at a Glance

| Skill | Pipeline | Frequency |
|-------|----------|-----------|
| `evolve-lite-recall` | Core session | Every session — first action |
| `evolve-lite-save-trajectory` | Core session | Every session — before learn |
| `evolve-lite-learn` | Core session | Every session — last action |
| `evolve-lite-subscribe` | Sharing | Once per repo |
| `evolve-lite-sync` | Sharing | Periodically |
| `evolve-lite-dedup` | Sharing / Maintenance | Before publishing; when recall feels noisy |
| `evolve-lite-publish` | Sharing | When ready to push to your fork |
| `evolve-lite-unsubscribe` | Sharing | When removing a subscription |
| `evolve-lite-create-tests` | Testing | Regenerate all fixtures from the current library |
| `evolve-lite-run-tests` | Testing | Run all three test suites on demand |
| `evolve-lite-test` | Testing | Generate fixtures + run all three suites |
| `evolve-lite-test-new-skills` | Testing | After `learn` — validate newly saved entities only |
| `evolve-lite-save` | Maintenance | After establishing a repeatable workflow |
| `evolve-lite-provenance` | Maintenance | To audit guideline influence |
| `evolve-manager` | Maintenance | To merge fork contributions into main |

---

## Modes

| Mode | Purpose |
|------|---------|
| **Evolve Lite** | Everyday working mode — enforces recall → work → save-trajectory → learn on every session |
| **Evolve Manager** | Maintainer mode — merges entity libraries from forks, runs regression tests, deduplicates |

---

## Core Session Pipeline (Evolve Lite)

Runs automatically every session. All four steps are mandatory and must run in order.

```
┌──────────────────────────────────────────────────────────────────────┐
│  1. recall          Surface stored guidelines & skills               │
│        ↓                                                             │
│  2. [do your work]                                                   │
│        ↓                                                             │
│  3. save-trajectory  Save conversation as JSON                       │
│        ↓                                                             │
│  4. learn            Extract & save new entities (see detail below)  │
└──────────────────────────────────────────────────────────────────────┘
```

| Skill | When it runs | What it does |
|-------|-------------|--------------|
| `evolve-lite-recall` | Start of every session | Searches `.evolve/entities/` by keyword overlap against the task and surfaces the top matching guidelines, atomic skills, and skill flows |
| `evolve-lite-save-trajectory` | After work, before learn | Saves the full conversation as a JSON file (OpenAI chat format) to `.evolve/trajectories/` |
| `evolve-lite-learn` | End of every session | Analyzes the trajectory, extracts reusable entities, generates test fixtures, and deduplicates — see expanded flow below |

---

## Inside `evolve-lite-learn`

`learn` is itself a multi-step pipeline. It runs every time a session ends.

```
Step 1 · load trajectory
      ↓
Step 2 · analyze conversation
  - identify task, steps taken, failures, retry loops, reusable outcomes
      ↓
Step 3 · identify errors & root causes
  - tool failures, permission errors, wrong initial approaches, silent failures
      ↓
Step 4 · decide whether to save a reusable artifact (script / workflow)
      ↓
Step 5 · extract entities  (3–5, prioritize failure-derived first)
  - guideline      → declarative approach preference, no executable steps
  - atomic-skill   → smallest self-contained executable procedure for one sub-problem
  - skill-flow     → ordered sequence of 2+ steps that recurs as a named unit
      ↓
Step 6 · save entities to .evolve/entities/{type}/{product}/
  - auto-detects product (watson-orchestrate, github, docker, kubernetes, general)
  - marks failure-derived skills with derived_from_failure: true
  - filters out common-sense / trivial skills
  - decomposes skill-flows into atomic skills automatically
      ↓
Step 7 · generate test fixtures
  - skips guideline entities (no executable outcome to test)
  - for each new atomic-skill / skill-flow:
      • 1 happy-path fixture  — normal conditions, rubric criteria as validation
      • 1–3 edge-case fixtures — boundary/failure conditions, rubric criteria annotated
  - saved to .evolve/tests/pseudo_conversations/{entity-slug}.json
  - append-only: existing test files are never overwritten
      ↓
Step 7a · run test gate  ← BLOCKS if pass rate < 80%
  - regenerates all pseudo-conversation fixtures (generate_skill_tests.py --all)
  - content evaluation  (run_skill_evaluation.py)  — skill must contain its prescribed commands
  - recall test          (run_recall_tests.py)      — skill must rank in top-3 for its trigger
  - gate threshold: 80% pass rate on both suites
  - FAIL → fix the entity file, re-run gate; do NOT continue to Step 8 until exit 0
      ↓
Step 8 · deduplicate against full library
  - compare new entities against all existing .evolve/entities/**/*.md
  - merge: combine similar entities into one richer file
  - discard: delete near-identical duplicates, keep richest
  - keep-all: no action when entities are genuinely distinct
  - tests are never deleted — if a slug changes, test files are copied to match
```

> **Note:** `learn` generates test fixtures (Step 7) and immediately runs the content + recall gate (Step 7a) before proceeding. If the gate fails, the learn workflow pauses for fixes before dedup runs. To run all three test suites (content, recall, baseline) at any time, use `evolve-lite-run-tests`.

### Test fixture types generated in Step 7

Each fixture is a JSON file in `.evolve/tests/pseudo_conversations/` and contains one or more test cases. There are five test types:

#### 1. Rubric-based execution test *(primary — generated by learn)*

The main test type for `atomic-skill` and `skill-flow` entities. Generated directly from the entity's `## Success Rubric` section.

- **Happy-path case**: runs the skill under normal conditions; `validation_criteria` copied verbatim from the rubric — no generic criteria invented
- **Edge-case tests (1–3)**: each covers a realistic boundary or failure condition (missing dependency, malformed input, already-existing output, partial environment). The rubric criteria are copied in and annotated to state which are expected to fail under that condition.

```json
{
  "test_type": "rubric_execution",
  "scenario": "Executing the skill end-to-end under normal conditions",
  "edge_case": false,
  "validation_criteria": [
    "exit code 0 after running the main command",
    "output file exists at the expected path",
    "no error lines appear in stdout"
  ]
}
```

#### 2. Trigger match test

Validates that the skill's `trigger` field correctly matches the scenarios it is meant to handle. Used to catch triggers that are too vague or use the wrong vocabulary.

```json
{
  "test_type": "trigger_match",
  "scenario": "User wants to import a Watson Orchestrate agent YAML",
  "expected_trigger_match": true,
  "skill_trigger": "When importing an agent YAML file into Watson Orchestrate..."
}
```

#### 3. Content completeness test *(fallback — for entities with no rubric)*

Used only for legacy entities that predate the `success_rubric` requirement. Checks that the skill content contains all required steps, executable commands, stated prerequisites, and clear expected outcomes.

#### 4. Skill composition test *(skill-flow only)*

Validates that a `skill-flow` entity's `atomic_skills` list is coherent:
- All referenced slugs resolve to real files under `.evolve/entities/atomic-skill/`
- Atomic skill content matches the steps described in the flow
- No circular dependencies

#### 5. Trajectory replay test

Validates that applying the skill to the original conversation trajectory that produced it would succeed — i.e., the skill would be recalled at the right point and its guidance matches what was actually done.

---

## Sharing Pipeline (Optional)

Use when you want to share your learned entities with the team or pull in others' guidelines.

> **Tests gate both dedup and publish.** `evolve-lite-dedup` runs a pre/post snapshot regression check around its dedup operation. `evolve-lite-publish` blocks on the content + recall gate (≥ 80% pass rate) before any entity is moved to the write-scope repo.

```
subscribe to fork (once)
      ↓
[work & learn sessions — each ends with the Step 7a gate]
      ↓
dedup  (with pre/post test snapshot — blocks on regression)
      ↓
publish  (blocks if test gate < 80%)
      ↓
open PR to main
      ↑
    sync  (pull latest from subscribed repos at any time)
```

| Skill | Command | What it does |
|-------|---------|--------------|
| `evolve-lite-subscribe` | `/evolve-lite-subscribe <url>` | Adds a repo (read or write scope) so Bob can pull from or publish to it |
| `evolve-lite-sync` | `/evolve-lite-sync` | Pulls the latest entities from all subscribed repos into your local `.evolve/` |
| `evolve-lite-dedup` | `/evolve-lite-dedup` | Two-phase cleanup before publishing — quality gate then merge/discard (see detail below) |
| `evolve-lite-publish` | `/evolve-lite-publish` | Stamps `visibility: public`, `owner`, `published_at` on each entity, moves it into the write-scope clone, commits, and pushes to the fork branch |
| `evolve-lite-unsubscribe` | `/evolve-lite-unsubscribe <url>` | Removes a repo subscription and deletes its local clone |

After publishing, [open a pull request](https://github.ibm.com/ce-artemis-2026/evobob-test/compare) from your fork's branch into `ce-artemis-2026/evobob-test:main`.

---

## Inside `evolve-lite-dedup`

Run before publishing to clean up a library that has grown from many `learn` sessions. Phase 2 will not run unless Phase 1 exits with code `0` — every skill must pass all blocking checks first.

The **recommended workflow** wraps dedup with a pre/post test snapshot so any regression introduced by merging or discarding skills is caught immediately:

```
pre-dedup
  ├─ generate_skill_tests.py --all        (refresh fixtures)
  ├─ check_tests.py --threshold 0.8       (must pass before dedup)
  └─ snapshot_test_results.py --out pre_dedup_snapshot.json

        ↓

Phase 1 — Quality Gate   (scripts/quality_gate.py)
  ├─ format check
  ├─ recall test
  ├─ skill evaluation
  ├─ naming check
  └─ version check  (warning only — non-blocking)

        ↓ exit 0 only ↓

Phase 2 — Refine         (scripts/refine.py)
  ├─ banality prune
  ├─ similarity clustering
  └─ merge / discard / keep-all decisions

        ↓

post-dedup
  ├─ check_tests.py --threshold 0.8       (rerun after entity changes)
  └─ snapshot_test_results.py             (compare → exits 1 on regression)
        --compare pre_dedup_snapshot.json
```

### Phase 1 — Quality Gate

Iterates every `.md` file under `.evolve/entities/` and runs the following checks. The gate exits `1` and blocks Phase 2 if any skill fails a blocking check.

#### Format check *(blocking)*

Each entity must have all of:
- `type` — must be exactly `guideline`, `atomic-skill`, or `skill-flow`
- `trigger` — at least 10 characters
- `owner` and `visibility` fields present and non-empty
- A non-empty content body

Skill-flows additionally require a non-empty `atomic_skills` field listing the slugs they compose.

Optional sections are validated when present:
- `## Requirements` — every package or CLI tool listed must be mentioned somewhere in the content body
- `## Imports` — every module or symbol listed must appear in the content body

#### Recall test *(blocking)*

Generates a realistic user question from the entity's `trigger` field using the same `trigger_to_realistic_question` logic used during live recall. Scores the entity against the full recall manifest using keyword-overlap heuristics.

**Pass condition**: the entity ranks `#1` or appears in the top 3 with a score `> 0`.

A failure here means the trigger uses words a user would not naturally type — the entity would never surface during a real session even if it is the correct answer. Fix by rewriting the trigger to match the symptom or task vocabulary a user would actually use.

#### Skill evaluation *(blocking)*

Checks internal self-consistency of the content:
- Derives `must_include` terms from the content (backtick commands, identifier names, key words)
- Verifies those terms appear in the content itself — **alignment score must be ≥ 0.5**
- For `skill-flow` entities: checks that every slug listed in `atomic_skills` resolves to a real `.md` file under `.evolve/entities/`

A failure indicates the skill's content references things it doesn't explain, or a skill-flow points to a dependency that doesn't exist.

#### Naming check *(blocking)*

Verifies the slug and trigger are well-formed:
- Slug follows `kebab-case`, 2–5 words, no punctuation
- Trigger does not contain prompt-injection patterns or overly generic phrases
- Slug does not start with trigger-style prefixes (`when-`, `if-`, `how-to-`)

#### Version check *(warning only — non-blocking)*

If `version > 1`, the `## Changelog` section must have at least that many entries. A mismatch is logged as a warning but does not block Phase 2.

#### Exit codes

| Code | Meaning |
|------|---------|
| `0` | All skills pass all blocking checks — Phase 2 runs |
| `1` | One or more skills failed a blocking check — Phase 2 is blocked until fixed |

---

### Phase 2 — Refine

Only runs after a clean Phase 1 exit. Operates on the same entity set.

#### Banality prune

Before any clustering, every `atomic-skill` and `guideline` entity is checked for banality and pruned if it matches **any** of these conditions:

| Condition | Example |
|-----------|---------|
| Content ≤ 30 chars with no backtick command | `"Activate the virtual environment."` |
| Content or trigger matches a common-sense pattern | `"install dependencies"`, `"git commit"`, `"run the application"` |
| Content is a near-verbatim restatement of the trigger (Jaccard ≥ 0.85) | Trigger: `"When activating the venv"` → Content: `"Activate the venv."` |

`skill-flow` entities are exempt from banality pruning because their step ordering is non-obvious even when individual steps are simple.

Use `--no-prune` to skip this step and review manually.

#### Similarity clustering

Groups remaining entities by **token-set Jaccard similarity** on the combined `trigger + content` text. Default clustering threshold is **0.45** — pairs at or above this score are grouped into a cluster.

| Jaccard score | Decision | Action |
|---------------|----------|--------|
| ≥ 0.75 | `discard` | Keep the entity with the longest content, delete the rest |
| ≥ 0.45 (threshold) | `merge` | Write a merged entity into the richest file; combined trigger joins all unique trigger phrases with `; ` so recall still fires on any original phrasing; delete the others |
| < 0.45 | `keep-all` | No action |

The "richest" entity in a cluster is always the one with the longest content body.

Run with `--interactive` to review each cluster manually and override the automatic decision (`k` keep-all, `m` merge, `d` discard, `s` skip).

#### Reports

Both phases write JSON reports to `.evolve/tests/dedup/` by default:

| File | Contents |
|------|----------|
| `quality_gate_report.json` | Per-skill results for format, recall, eval, and naming checks |
| `refine_report.json` | Per-cluster decisions — which entities were merged, discarded, or kept, with file paths |

---


## Testing Pipeline

`learn` now runs the content + recall gate automatically at Step 7a. `evolve-lite-run-tests` is a separate on-demand command that runs all three suites at any time. `check_tests.py` is the shared gate script used by learn, publish, and dedup.

```
learn  →  fixtures written to .evolve/tests/pseudo_conversations/
                  ↓  (Step 7a — runs automatically inside learn)
        check_tests.py --threshold 0.8
          ├─ run_skill_evaluation.py   (content test)   ← BLOCKS learn if < 80%
          └─ run_recall_tests.py       (recall test)    ← BLOCKS learn if < 80%

                  ↓  (on-demand, any time)
        evolve-lite-run-tests
          ├─ run_skill_evaluation.py   (content test)
          ├─ run_recall_tests.py       (recall test)
          └─ run_baseline_tests.py     (baseline / trigger-discrimination test)
```

**Gate thresholds used across the pipeline:**

| Trigger point | Script | Threshold | Blocking? |
|---|---|---|---|
| After `learn` Step 7 | `check_tests.py` | ≥ 80% both suites | Yes — Step 8 blocked |
| Before `publish` | `check_tests.py` | ≥ 80% both suites | Yes — publish blocked |
| Before/after `dedup` | `check_tests.py` + `snapshot_test_results.py` | ≥ 80% + no regression | Yes — Phase 2 / completion blocked |

To regenerate fixtures manually (e.g. after editing a skill's trigger):

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py --all
```

### Fixing gate failures

When `check_tests.py` exits 1, the output marks each failure as either a **content evaluation** failure or a **recall** failure.

**Content evaluation failure** — `score < 0.5`, `missed=[…]`

The fixture's `must_include` list contains command terms that are absent from the skill content.

- Add the missing commands/flags to the content body of the entity file.
- If the term was over-extracted and the skill is correct without it, regenerate the fixture for just that skill: `generate_skill_tests.py <path-to-entity.md>`
- If `violated=[…]` is non-empty, remove the flagged phrase from the skill content.

**Recall failure** — `rank > 3`, `matched_terms` contains only short generic tokens

The `trigger` field uses vocabulary that doesn't overlap with what a user would type.

- Rewrite the trigger to include the symptom, error message, or specific command/flag name the user would describe.
- Check the `top5` list in the output — if other skills rank above yours, add the vocabulary that distinguishes your skill from those.
- Regenerate the fixture after rewriting: `generate_skill_tests.py <path-to-entity.md>`

Then re-run `check_tests.py --threshold 0.8 --verbose` to verify.

---

### Content test (`run_skill_evaluation.py`)

**What it checks:** the skill content is self-consistent — it contains the commands and terms it claims to prescribe.

**How it works:**
1. Loads each fixture from `.evolve/tests/pseudo_conversations/`
2. The fixture's `conversation` field has a `system` message with the skill injected inside a `<recalled_skill>` block, and a `user` message with the scenario question
3. Instead of calling a live LLM, the runner uses the skill's own content as the simulated agent response — because a well-formed skill that contains its prescribed commands will pass, and gaps will surface
4. Checks the response against the fixture's `expected_behaviour.must_include` list: each term is lowercased, angle-bracket placeholders stripped, then substring-searched in the response
5. **Pass condition:** `alignment_score = matched / total ≥ 0.5` and no `must_not_include` terms found
6. Reports per-skill token breakdown (preamble / skill / user / completion) and latency

**Output:** `.evolve/tests/evaluation/report.json`

---

### Recall test (`run_recall_tests.py`)

**What it checks:** the skill surfaces when it should — given the user's question, the skill ranks in the top-K of the recall manifest.

**How it works:**
1. Builds the live recall manifest from all entities under `.evolve/entities/`
2. For each fixture, takes the `user` message from the conversation
3. Scores every entry in the manifest against that user message using **weighted keyword overlap**: tokenises both trigger and user message (lowercase, stop words removed, tokens < 3 chars dropped), finds the intersection, scores as `sum(len(term) for term in matched_terms)` — longer terms score higher, filtering out accidental short-word matches
4. Ranks all manifest entries by descending score
5. Finds the rank of the expected skill slug in that ranking
6. **Pass condition (default):** the expected skill is in the **top 3** with a score `> 0` (Recall@3). Configurable to Recall@1 or Recall@5 with `--top-k`

**What a failure means:** the trigger uses vocabulary that doesn't overlap with what a user would actually type. Fix by rewriting the trigger to include the symptom words or task keywords the user would use.

**Output:** `.evolve/tests/evaluation/recall_report.json` — reports Recall@1, @3, and @5

---

### Baseline test (`run_baseline_tests.py`)

**What it checks:** the skill is *necessary* — a naive agent responding *without* the skill injected would miss the key guidance.

**How it works:**
1. For each fixture, takes only the `user` message — **no system prompt, no skill injected**
2. Looks up a pre-written naive baseline response for the skill slug (a "reasonable-but-incomplete" answer representing what a competent generic agent would say without the skill)
3. Checks whether that naive response contains the fixture's `must_include` terms using the same alignment scorer as the content test
4. **Interpretation is inverted from the content test:**
   - `FAIL` (naive agent misses the terms) → **skill IS necessary** — the trigger correctly identifies a non-obvious situation
   - `PASS` (naive agent gets it right) → skill may not add value — consider whether it is worth keeping

**Important:** baseline test "failures" are not failures in the traditional sense — a skill that a naive agent would get wrong *is the goal*. The test exits `0` regardless of pass/fail count. It only exits `1` if no responses could be evaluated at all.

**Output:** `.evolve/tests/evaluation/baseline_report.json`

---

## Maintenance Pipeline (Evolve Manager mode)

For maintainers merging fork contributions into `main`. Switch to **Evolve Manager** mode in Bob and run `/evolve-manager`.

### Full pipeline

```
[0] PR gate
      ↓  skip forks without an open PR against the main repo
accepted forks only
      ↓
clone + stage fork entities  (sparse checkout of .evolve/entities/ only)
      ↓
snapshot main-repo entity manifest  →  main_entity_slugs.json
      ↓
versioning-aware merge  (see below)
      ↓
[A] quality gate on merged test fixtures
      ↓
[B] rubric tests on main-repo entities only  →  baseline_rate
      ↓
[C] full skill dedup  (both phases)
      ↓
[D] rubric tests on main-repo entities only  →  post_rate
      ↓
[E] threshold gate
      PASS  →  commit merged entities to .evolve/entities/
      FAIL  →  show diff, pause for user decision (keep or rollback)
      ↓
write merge_report.json  (always, even on error or skip)
```

Fork-sourced entities are **never** counted against the regression threshold. Only entities that existed in the main repo before the merge are subject to the gate.

### Versioning-aware merge

When the same entity slug exists in both the main repo and a fork, the script compares them using token-set Jaccard similarity on `trigger + content`:

| Jaccard score | Action |
|---|---|
| ≥ `--version-diff-threshold` (default 0.5) | Fork content replaces main-repo content outright |
| < `--version-diff-threshold` | Dual-section entity written: `## Current Version` (fork) + `## Previous Version` (original) |

The entity's `version` frontmatter is set to the fork's version. A `base_version` field records the original main-repo version for traceability.

### Regression gate

The threshold gate applies only to entities listed in the pre-merge snapshot (`main_entity_slugs.json`). For dual-section entities, rubric tests run against the full merged file using the **original main-repo `must_include` terms** — confirming the merged skill still satisfies what the main-repo version promised.

Default threshold is `1.0` (no regressions allowed). Relax with `--threshold 0.8` to allow up to 20% regression.

### Exit codes

| Code | Meaning | Action |
|---|---|---|
| `0` | Merge succeeded | Entities are live in `.evolve/entities/` |
| `1` | Hard failure | Fix the reported error before re-running |
| `2` | Threshold breach | Main-repo pass rate dropped — keep or rollback (user decides) |

### Rollback

A backup of the pre-merge `.evolve/entities/` is written before any live file is touched:

```bash
rm -rf .evolve/entities/
cp -r .evolve/tmp/pre-merge-backup/ .evolve/entities/
```

### Key flags

| Flag | Default | Description |
|---|---|---|
| `--fork-dirs` | (required) | Space-separated list of pre-cloned fork directories |
| `--threshold` | `1.0` | Min rubric pass rate for main-repo tests |
| `--version-diff-threshold` | `0.5` | Jaccard below which dual-section merging is used |
| `--dry-run` | off | Show all decisions without writing any files |
| `--no-require-pr` | off | Disable the PR gate — merge all provided forks |
| `--main-repo` | auto | Upstream repo as `owner/repo` (auto-detected from git `origin`) |
| `--github-token` | env | GitHub token (falls back to `GITHUB_TOKEN` env var) |

### Reports

| File | Contents |
|---|---|
| `.evolve/tests/dedup/merge_report.json` | Full run summary — PR gate, accepted/skipped forks, merge decisions, pass rates, outcome |
| `.evolve/tests/dedup/quality_gate_report.json` | Phase 1 format/recall/eval results |
| `.evolve/tests/dedup/refine_report.json` | Phase 2 cluster decisions (merge/discard/keep) |
| `.evolve/tests/evaluation/report.json` | Pre-dedup rubric test results |
| `.evolve/tests/evaluation/report_post.json` | Post-dedup rubric test results |

### Other maintenance skills

| Skill | What it does |
|-------|-------------|
| `evolve-lite-save` | Captures a successful workflow from a session and saves it as a new named skill with `SKILL.md` and helper scripts |
| `evolve-lite-provenance` | Analyzes trajectories and audit events to record whether recalled guidelines actually influenced completed sessions |

