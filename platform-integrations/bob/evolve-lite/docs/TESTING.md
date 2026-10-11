# Skill Testing

This project uses the `evolve-lite-test` framework to validate that entities in the `.evolve/` library are correct, discoverable, and genuinely useful. Three complementary tests are run against every `atomic-skill` and `skill-flow` entity.

## Three test types

### 1. Content test — *does the skill contain what it prescribes?*

Each skill is checked for self-consistency. The backtick command strings in the skill content are extracted and matched back against the skill body. A passing skill contains every command and term it claims to prescribe.

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py --all
python3 .bob/skills/evolve-lite-test/scripts/run_skill_evaluation.py --verbose
```

**Pass condition:** `alignment_score ≥ 0.5` and no `must_not_include` terms violated.

---

### 2. Recall test — *does the right skill surface for the right question?*

The live entity manifest is scored against a trigger-derived user question for each skill. The skill must appear in the top 3 results (Recall@3) with a score greater than zero.

```bash
python3 .bob/skills/evolve-lite-test/scripts/run_recall_tests.py --verbose
```

**Pass condition:** the expected skill ranks in the top 3 against its trigger question using keyword-overlap scoring.

A recall failure means the `trigger` field uses vocabulary a user would not naturally type. Fix by rewriting the trigger to include the symptom or command words the user would actually describe.

---

### 3. Baseline test — *is the skill actually necessary?*

An agent is asked the trigger question **without the skill injected**. If a naive agent already gives the right answer, the skill covers common knowledge. If it misses key guidance, the skill is genuinely valuable.

```bash
python3 .bob/skills/evolve-lite-test/scripts/run_baseline_tests.py --simulate
```

**Interpretation:** a baseline "failure" (naive agent misses the guidance) is the desired outcome — it means the skill encodes non-obvious knowledge. The test exits `0` regardless of how many skills are flagged as necessary; it only exits `1` if no responses could be evaluated at all.

---

## When tests run automatically

Tests are not just on-demand tools — they are embedded as gates throughout the pipeline:

| Trigger point | Gate script | Threshold | Blocks |
|---|---|---|---|
| After `learn` Step 7 | `check_tests.py` | ≥ 80% both suites | Step 8 (dedup) |
| Before `publish` | `check_tests.py` | ≥ 80% both suites | Publishing |
| Before/after `dedup` | `check_tests.py` + `snapshot_test_results.py` | ≥ 80% + no regression | Phase 2 / completion |

---

## Commands

| Command | What it does |
|---|---|
| `/evolve-lite-run-tests` | Run all three test suites against the current entity library |
| `/evolve-lite-create-tests` | Regenerate all fixtures from the current entity library |
| `/evolve-lite-test` | Generate fixtures + run all three tests |
| `/evolve-lite-test-new-skills` | Run after `/evolve-lite-learn` — test only newly saved skills |
| `/evolve-lite-test-recall` | Recall ranking test only |
| `/evolve-lite-test-trigger` | Baseline (necessity) test only |

---

## Test fixtures

All fixtures live in `.evolve/tests/pseudo_conversations/` — one JSON per entity. Each fixture contains:

- A **skill-specific user question** derived from the entity's `trigger` field
- The **skill content** injected as system context (for content tests)
- `must_include` — the exact command strings the skill must produce
- `must_not_include` — terms the skill explicitly forbids

Fixtures are generated automatically by `learn` and can be regenerated at any time:

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py --all
```

To regenerate a single entity's fixture after editing its trigger or content:

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py <path/to/entity.md>
```

---

## Test reports

Reports are written to `.evolve/tests/evaluation/`:

| Report | Contents |
|--------|----------|
| `report.json` | Content evaluation — per-skill alignment scores, matched/missed terms, pass rate |
| `recall_report.json` | Recall ranking — Recall@1, @3, @5 rates across all fixtures |
| `baseline_report.json` | Baseline necessity — which skills a naive agent would get wrong |

---

## Fixing gate failures

**Content evaluation failure** — `score < 0.5`, `missed=[…]`

The fixture's `must_include` list contains command terms absent from the skill content.

- Add the missing commands or flags to the entity file's content body.
- If a term was over-extracted and the skill is correct without it, regenerate just that fixture: `generate_skill_tests.py <path-to-entity.md>`
- If `violated=[…]` is non-empty, remove the flagged phrase from the skill content.

**Recall failure** — `rank > 3`

The `trigger` field uses vocabulary that doesn't overlap with what a user would type.

- Rewrite the trigger to include the symptom, error message, or specific command the user would describe.
- Check the `top5` list in the output — add vocabulary that distinguishes your skill from those ranked above it.
- Regenerate the fixture after rewriting: `generate_skill_tests.py <path-to-entity.md>`

Then re-run `check_tests.py --threshold 0.8 --verbose` to verify.

---

## After adding a new skill

Run `/evolve-lite-test-new-skills` immediately after `/evolve-lite-learn` to generate a fixture and validate the new entity before it enters the library.
