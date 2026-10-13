# Evolve Lite for Bob

A Bob integration that gives IBM Bob a persistent learning layer — each session recalls stored knowledge, then extracts and saves new learnings automatically.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

📋 **[Skills & Modes Pipeline →](docs/PIPELINE.md)** — how all skills connect and the full learn/dedup/test flow

🧪 **[Testing →](docs/TESTING.md)** — how the entity library is validated

## How It Works

Every session in **Evolve Lite** mode follows four mandatory steps:

1. **Recall** — surface stored guidelines and skills relevant to your task
2. **Work** — complete your task with Bob as normal
3. **Save trajectory** — capture the full conversation as structured JSON
4. **Learn** — extract reusable entities, run the quality gate, and merge into the library

Over time the library accumulates your team's hard-won knowledge — non-obvious commands, error recovery patterns, product-specific workflows — and recalls it automatically at the start of each session.

## Installation

Run the installer from the `altk-evolve` repository root:

```bash
bash platform-integrations/install.sh install --platform bob --mode lite
```

Or remotely:

```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh \
  | bash -s -- install --platform bob --mode lite
```

## Repository Layout

```
.bob/                        Bob skill definitions and mode configuration
  commands/                  Slash-command definitions (one per skill)
  lib/evolve-lite/           Shared Python libraries
    entity_io.py             Entity read/write, product registry, banality checks
    config.py                evolve.config.yaml reader/writer
    audit.py                 Audit log helpers
    trajectory_extractor.py  Reads trajectories directly from Bob task logs
  skills/                    Skill implementations (SKILL.md + scripts/)

.evolve/                     Runtime data (created on first use)
  entities/
    atomic-skill/            Single-step reusable procedures, organised by domain
    guideline/               Declarative approach preferences, organised by domain
    skill-flow/              Multi-step ordered workflows, organised by domain
    subscribed/              Entities from subscribed repos (gitignored)
  tests/
    pseudo_conversations/    Test fixtures (one JSON per entity)
    evaluation/              Test reports (content, recall, baseline)
    dedup/                   Deduplication phase reports
  trajectories/              Saved conversation JSON files
  audit.log                  Recall audit log
```

## Entity Types

Evolve Lite stores three types of entities:

- **`guideline`** — a declarative preference that shapes how an agent chooses between approaches. No executable steps. Example: *"prefer CLI over MCP server when both are available"*.
- **`atomic-skill`** — the smallest self-contained executable procedure that solves one focused sub-problem. Includes steps, dependencies, concrete examples, and a success rubric.
- **`skill-flow`** — a named, recurring ordered sequence of steps, where each step maps to an existing atomic skill or inlined operation. Order matters.

## Skills Included

| Skill | Command | Purpose |
|---|---|---|
| `evolve-lite-recall` | `/evolve-lite-recall` | Surface stored entities relevant to the current task |
| `evolve-lite-save-trajectory` | `/evolve-lite-save-trajectory` | Save the conversation as JSON to `.evolve/trajectories/` |
| `evolve-lite-learn` | `/evolve-lite-learn` | Extract entities, run quality gate, merge into library |
| `evolve-lite-dedup` | `/evolve-lite-dedup` | Two-phase cleanup: quality gate then merge/discard |
| `evolve-lite-create-tests` | `/evolve-lite-create-tests` | Generate test fixtures for all entities |
| `evolve-lite-run-tests` | `/evolve-lite-run-tests` | Run content, recall, and baseline test suites |
| `evolve-lite-test` | `/evolve-lite-test` | Generate fixtures + run all three suites |
| `evolve-lite-test-new-skills` | `/evolve-lite-test-new-skills` | Test only newly saved entities after `learn` |
| `evolve-lite-publish` | `/evolve-lite-publish` | Push entities to a write-scope git repo |
| `evolve-lite-subscribe` | `/evolve-lite-subscribe` | Add a read or write-scope git repo |
| `evolve-lite-sync` | `/evolve-lite-sync` | Pull latest entities from all subscribed repos |
| `evolve-lite-unsubscribe` | `/evolve-lite-unsubscribe` | Remove a repo subscription |
| `evolve-lite-provenance` | `/evolve-lite-provenance` | Audit which recalled entities influenced sessions |
| `evolve-lite-save` | `/evolve-lite-save` | Capture a session workflow as a new reusable skill |
| `evolve-manager` | `/evolve-manager` | Merge fork entity libraries into main (maintainer mode) |

## Sharing Entities

Entities are shared via git. Add a `evolve.config.yaml` at your project root:

```yaml
identity:
  user: yourname

repos:
  - name: memory
    scope: write
    remote: git@github.com:yourname/evolve-memory.git
    branch: main
  - name: team
    scope: read
    remote: git@github.com:myorg/evolve-guidelines.git
    branch: main
```

Then use `/evolve-lite-subscribe`, `/evolve-lite-publish`, and `/evolve-lite-sync` to manage sharing.

The `.evolve/entities/subscribed/` directory is excluded from version control — the skills automatically gitignore it.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `EVOLVE_DIR` | `.evolve` | Override the evolve data directory location |
| `EVOLVE_DEBUG` | unset | Set to `1` to enable debug logging to `/tmp/evolve-<uid>/evolve-plugin.log` |

## Documentation

- **[PIPELINE.md](docs/PIPELINE.md)** — complete reference for all skills, modes, and how they chain together
- **[TESTING.md](docs/TESTING.md)** — how the entity library is validated (content, recall, and baseline tests)
- **[atomic_skill_evaluation_plan.md](docs/atomic_skill_evaluation_plan.md)** — design notes for the atomic skill evaluation framework
- **[bob-management-mode-plan.md](docs/bob-management-mode-plan.md)** — design notes for Evolve Manager mode
