# Evolve Lite for Claw

Evolve Lite for Claw is a small skill bundle for capturing and reusing lessons from work done in a project. In the Claw integration, it is skill-driven: you invoke the skills when you want to save guidance, recall guidance, export a trajectory, or turn a successful session into a reusable skill.

It does not currently install or rely on active hooks as part of the documented workflow.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

## What This Plugin Provides

After installation and enablement, this plugin gives Claw four skills:

- `evolve-lite:learn` analyzes the current conversation, extracts high-value guidelines, and saves them as markdown entities.
- `evolve-lite:recall` loads stored entities from the current project so the agent can review and apply the relevant ones.
- `evolve-lite:save-trajectory` exports the current conversation into an OpenAI-style trajectory JSON file.
- `evolve-lite:save` turns a successful session into a new reusable skill under Claw's skills directory.

The plugin is mainly a packaging and distribution mechanism for these skills and their helper scripts.

## Installation

Install the plugin with the project installer or by installing the plugin directory into Claw.

If you use the project installer:

```bash
./platform-integrations/install.sh install --platform claw-code
```

After installation:

1. Open `claw`
2. Run `/plugin enable evolve-lite`
3. Run `/plugin list` to confirm it is enabled

## Skill Guide

### `evolve-lite:learn`

Use this at the end of a task when the conversation exposed something worth remembering.

What it does:

- Reviews the current conversation in forked context
- Extracts up to five guidelines
- Focuses on shortcuts, error prevention, and user corrections
- Saves them into `.evolve/entities/`

The helper script writes markdown files and deduplicates by normalized content.

Stored format:

```text
.evolve/entities/
  guideline/
    some-guideline.md
```

Each entity is stored as markdown with frontmatter such as:

```markdown
---
type: guideline
trigger: When working in sandboxed environments
---

Use Python libraries for this task instead of relying on unavailable system tools.

## Rationale

This avoids failures caused by missing host utilities.
```

### `evolve-lite:recall`

Use this when you want the agent to review previously saved guidance before or during a task.

What it does:

- Loads all entity markdown files under `.evolve/entities/`
- Formats them into a readable prompt block
- Lets the agent decide which guidance is relevant

This is a manual recall flow in the current Claw integration. The plugin README should not be read as implying automatic injection.

### `evolve-lite:save-trajectory`

Use this when you want a durable record of the current conversation for analysis, fine-tuning prep, or later guideline generation.

What it does:

- Walks the current conversation in forked context
- Converts it into an OpenAI chat-completions-style JSON structure
- Writes the result to `.evolve/trajectories/trajectory_<timestamp>.json`

Output location:

```text
.evolve/trajectories/
  trajectory_2026-04-10T12-00-00.json
```

### `evolve-lite:save`

Use this after a successful session when you want to preserve the workflow itself as a reusable Claw skill.

What it does:

- Analyzes the successful session
- Extracts a reusable workflow
- Generates a new `SKILL.md`
- Optionally generates helper Python scripts
- Saves the result into Claw's skills directory

Generated skills are stored under:

- project-level: `.claw/skills/<skill-name>/` when applicable
- user-level: `~/.claw/skills/<skill-name>/`

## Storage Locations

This plugin uses a few simple storage locations:

- `.evolve/entities/` for saved guidance entities
- `.evolve/trajectories/` for exported conversation trajectories
- `.claw/skills/` or `~/.claw/skills/` for installed/generated skills

If `EVOLVE_DIR` is set, entity and trajectory storage follows that override instead of the default `.evolve/` directory.

## Helper Scripts

The bundled skills use small helper scripts:

- `skills/learn/scripts/save_entities.py` saves entity JSON to markdown files
- `skills/recall/scripts/retrieve_entities.py` reads and formats stored entities
- `skills/save-trajectory/scripts/save_trajectory.py` writes trajectory JSON files

The Claw skill docs resolve these scripts from either:

- `.claw/skills/...`
- `~/.claw/skills/...`

so the skills work in both project-level and user-level installs.

## Plugin Structure

```text
evolve-lite/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   ├── learn/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── save_entities.py
│   ├── recall/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── retrieve_entities.py
│   ├── save/
│   │   └── SKILL.md
│   └── save-trajectory/
│       ├── SKILL.md
│       └── scripts/
│           └── save_trajectory.py
├── lib/
│   ├── __init__.py
│   └── entity_io.py
└── README.md
```
