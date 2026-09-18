---
description: Create pseudo-conversation test fixtures for all atomic skills, or for specific new skill files. Run this after evolve-lite-learn to generate tests for newly saved skills.
---

To create test fixtures for ALL skills:

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py --all
```

To create fixtures for specific new skill files (e.g. just saved by `evolve-lite-learn`):

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py <path-to-skill.md> [<path2.md> ...]
```

Each fixture is written to `.evolve/tests/pseudo_conversations/<skill-slug>.json` and contains:
- A skill-specific user question derived from the skill's trigger
- The `must_include` command terms extracted from the skill content
- The skill content ready to be injected as system context

After creating fixtures, run `/evolve-lite-run-tests` to evaluate them.
