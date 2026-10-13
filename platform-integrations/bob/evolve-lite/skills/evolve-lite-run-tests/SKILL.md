---
name: evolve-lite-run-tests
description: >-
  Run all skill tests — content, recall, and baseline — against the existing
  pseudo-conversation fixtures.
metadata:
  user-invocable: true
  disable-model-invocation: true
---

Run all three tests against existing fixtures:

```bash
python3 .bob/skills/evolve-lite-test/scripts/run_skill_evaluation.py --verbose
python3 .bob/skills/evolve-lite-test/scripts/run_recall_tests.py --verbose
python3 .bob/skills/evolve-lite-test/scripts/run_baseline_tests.py --simulate
```

**What each test checks:**

| Test | Script | Checks |
|---|---|---|
| Content | `run_skill_evaluation.py` | Skill contains the commands it prescribes |
| Recall | `run_recall_tests.py` | Skill surfaces in top 3 when its scenario is described |
| Baseline | `run_baseline_tests.py` | Agent without the skill misses the key guidance |

**Reports written to `.evolve/tests/evaluation/`:**
- `report.json` — content test
- `recall_report.json` — recall test
- `baseline_report.json` — baseline test

To regenerate fixtures before running, use `/evolve-lite-create-tests` first.
