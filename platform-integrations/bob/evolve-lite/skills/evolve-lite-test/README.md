# Evolve Lite Test Skill

**Testing framework for validating atomic skills through pseudo-conversation evaluation.**

## Overview

The `evolve-lite-test` skill provides three test types for atomic skills:

1. **Content test** — does the skill contain the commands it prescribes?
2. **Recall test** — does the skill surface in the top 3 when its scenario is described?
3. **Trigger test** — would an agent get it right *without* the skill injected?

---

## Quick Start

Run the full suite:

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py --all
python3 .bob/skills/evolve-lite-test/scripts/run_skill_evaluation.py --verbose
python3 .bob/skills/evolve-lite-test/scripts/run_recall_tests.py --verbose
```

Or use the command: `/evolve-lite-test`

---

## Test Types

### Content test — `run_skill_evaluation.py`

Checks self-consistency: the skill's backtick command strings are extracted and matched against the skill's own content.

- ✅ **Pass** — skill contains all its prescribed commands
- ❌ **Fail** — skill is too vague or missing a key command

### Recall test — `run_recall_tests.py`

Scores every manifest entry against the skill's trigger-specific user question. Checks the expected skill ranks in the top 3.

- ✅ **Pass** — skill is rank 1–3 for its scenario
- ❌ **Fail** — trigger needs more distinctive vocabulary

### Baseline test — `run_baseline_tests.py`

Asks an agent the trigger question *without* the skill injected, then evaluates the response.

- ✅ **Pass** (agent gets it right) — skill covers common knowledge; consider whether it adds value
- ❌ **Fail** (agent misses it) — skill IS necessary; captures non-obvious guidance

**A healthy skill library should have mostly ❌ here** — each skill should cover something an agent wouldn't naturally get right on its own.

---

## Scripts

| Script | Purpose |
|---|---|
| `generate_pseudo_conversations.py` | Generate fixtures for all skills at once |
| `generate_skill_tests.py` | Generate fixtures for specific skill files (use after `evolve-lite-learn`) |
| `run_skill_evaluation.py` | Content test |
| `run_recall_tests.py` | Recall test |
| `run_trigger_tests.py` | Trigger test |

---

## Commands

| Command | When to use |
|---|---|
| `/evolve-lite-test` | Full suite — all three tests |
| `/evolve-lite-test-new-skills` | After `evolve-lite-learn` — test newly saved skills only |
| `/evolve-lite-test-recall` | Recall test only |
| `/evolve-lite-test-trigger` | Trigger test only |

---

## Directory Structure

```
.bob/skills/evolve-lite-test/
├── README.md                               # This file
├── SKILL.md                                # Skill definition
├── HOW_EVALUATION_WORKS.md                 # Evaluation logic details
└── scripts/
    ├── generate_pseudo_conversations.py    # Generate fixtures for all skills
    ├── generate_skill_tests.py             # Generate fixtures for specific skills
    ├── run_skill_evaluation.py             # Content test
    ├── run_recall_tests.py                 # Recall test
    └── run_trigger_tests.py                # Trigger test

.evolve/tests/
├── pseudo_conversations/                   # One fixture JSON per skill + test_suite.csv
└── evaluation/
    ├── subagent_prompt_template.md
    ├── report.json                         # Content test summary
    ├── recall_report.json                  # Recall test summary
    ├── trigger_report.json                 # Trigger test summary
    └── results/                            # Per-skill result files
```

---

## Current Test Coverage (6 atomic skills)

- 📊 **Content test:** 6/6 passing (100%)
- 📊 **Recall test:** 6/6 passing (100%), 5/6 rank-1
- 📊 **Trigger test:** 1/6 skills are necessary (`handle-interactive-prompts`)

---

## After adding a new skill

Run `/evolve-lite-test-new-skills` immediately after `/evolve-lite-learn` to generate a fixture and validate the new skill before it enters the library.
