---
name: evolve-lite-test-new-skills
description: >-
  Generate pseudo-conversation test fixtures for newly saved atomic skills. Run
  this immediately after evolve-lite-learn has finished saving entities.
metadata:
  user-invocable: true
  disable-model-invocation: true
---

After `evolve-lite-learn` has saved new skill entities, generate a test fixture
for each new atomic-skill file by running:

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py <path-to-new-skill.md> [<path2.md> ...]
```

Pass the exact file paths that `save_entities.py` just wrote. The script will:
1. Read each file and confirm it is an `atomic-skill` (skips guidelines and skill-flows)
2. Derive a realistic trigger-based user question from the skill's `trigger` field
3. Extract `must_include` terms (backtick command strings) and `must_not_include` terms (negation patterns) from the skill content
4. Write a fixture JSON to `.evolve/tests/pseudo_conversations/<skill-slug>.json`

If you don't have the exact paths, regenerate fixtures for all skills at once:

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py --all
```

To validate the new fixtures immediately after generating them:

```bash
python3 .bob/skills/evolve-lite-test/scripts/run_skill_evaluation.py --verbose
```

A passing result (score ≥ 0.5, no constraint violations) means the skill is
self-consistent — its content contains the commands it claims to prescribe.
A failure signals that the skill content may be too vague or missing a key command.
