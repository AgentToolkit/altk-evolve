# Evolve Lite for Claw

Evolve Lite for Claw is a small skill bundle for capturing and reusing lessons from work done in a project. In the Claw integration, it is skill-driven: you invoke the skills when you want to save guidance, recall guidance, export a trajectory, share guidelines with other projects or teammates, or turn a successful session into a reusable skill.

It does not currently install or rely on active hooks as part of the documented workflow.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

## What This Plugin Provides

After installation and enablement, this plugin gives Claw the following skills:

Local capture and recall:

- `evolve-lite:learn` analyzes the current conversation, extracts high-value guidelines, and saves them as markdown entities.
- `evolve-lite:recall` loads stored entities from the current project so the agent can review and apply the relevant ones.
- `evolve-lite:save-trajectory` exports the current conversation into an OpenAI-style trajectory JSON file.
- `evolve-lite:save` turns a successful session into a new reusable skill under Claw's skills directory.

Sharing guidelines via git repos:

- `evolve-lite:subscribe` adds a shared guidelines repo to the unified `repos:` list (read-scope subscription or write-scope publish target) and clones it under `.evolve/entities/subscribed/`.
- `evolve-lite:publish` moves a private guideline into a configured **write-scope** repo, stamps `visibility`/`owner`/`published_at`, and commits + pushes it.
- `evolve-lite:sync` pulls the latest changes from every configured repo (read-scope clones use `git fetch` + `git reset --hard`; write-scope clones use `git fetch` + `git rebase` to preserve unpushed publishes).
- `evolve-lite:unsubscribe` removes a configured repo and deletes its local clone, with an extra warning before removing a write-scope repo.

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

## Sharing Guidelines

Evolve Lite treats shared guidelines as multi-reader / multi-writer git
databases. A single unified `repos:` list in `evolve.config.yaml` describes
every external guideline repo you read from or publish to; each entry has a
`scope` of `read` (subscribe only) or `write` (publish target, also synced).

### Setup

Sharing requires an `evolve.config.yaml` at the project root. The subscribe
and publish skills will help you create one if it is missing. Structure:

```yaml
identity:
  user: yourname          # used to stamp ownership on published guidelines
repos:
  - name: memory
    scope: write
    remote: git@github.com:yourname/evolve-memory.git
    branch: main
    notes: public memory for my open-source projects
  - name: org-memory
    scope: read
    remote: git@github.com:acme/org-memory.git
    branch: main
    notes: private memory shared only within my org
```

- `scope: read` — pulled on sync. Cannot be published to.
- `scope: write` — publish target **and** pulled on sync (so you see
  everything pushed to it, including by other writers).

The `.evolve/` directory is kept out of version control — the skills
automatically add it to `.gitignore`.

### Subscribing, Publishing, Syncing, Unsubscribing

- `evolve-lite:subscribe` adds either a read-only subscription or a
  write-scope publish target. The repo is cloned into
  `.evolve/entities/subscribed/<name>/` so recall can pick it up
  immediately. Repo names must use only letters, numbers, `.`, `_`, and
  `-`.
- `evolve-lite:publish` lists files in `.evolve/entities/guideline/`,
  prompts for which to publish, then for each selection: stamps
  `visibility: public`/`owner`/`published_at`/`source`, moves it under
  `.evolve/entities/subscribed/{repo}/guideline/`, and commits + pushes.
- `evolve-lite:sync` pulls the latest changes from every configured repo.
  Read-scope repos use `git fetch` + `git reset --hard`, so the local
  clone always matches the remote exactly. Write-scope repos use
  `git fetch` + `git rebase` so any unpushed local publish commits are
  preserved.
- `evolve-lite:unsubscribe` removes any repo and deletes its local clone.
  Warns before removing a write-scope repo (unpushed publishes would be
  lost).

### Sharing Storage Layout

```text
.evolve/
  entities/
    guideline/
      private-guideline.md
    subscribed/
      memory/                 # write-scope clone — publishes land here
        guideline/
          my-published-guideline.md
      alice/                  # read-scope clone
        guideline/
          her-guideline.md
```

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
- `skills/subscribe/scripts/subscribe.py` clones a guidelines repo and registers it in `evolve.config.yaml`
- `skills/publish/scripts/publish.py` stamps and moves a private guideline into a write-scope clone
- `skills/sync/scripts/sync.py` pulls all configured repos (write-scope rebases preserve unpushed work)
- `skills/unsubscribe/scripts/unsubscribe.py` removes a repo from config and deletes its clone

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
│   ├── save-trajectory/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── save_trajectory.py
│   ├── subscribe/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── subscribe.py
│   ├── publish/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── publish.py
│   ├── sync/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── sync.py
│   └── unsubscribe/
│       ├── SKILL.md
│       └── scripts/
│           └── unsubscribe.py
├── lib/
│   ├── __init__.py
│   ├── audit.py
│   ├── config.py
│   └── entity_io.py
├── hooks/
│   └── retrieve_entities.sh
└── README.md
```
