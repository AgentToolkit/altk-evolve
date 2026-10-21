# Evolve Lite Plugin for Claude Code

A plugin that makes Claude Code's native memory **shareable** and **auditable**.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

## Features

Claude Code already has native, self-directed memory: it decides what to recall
at the start of a task and what to save at the end. Evolve Lite does not replace
or wrap that — it adds the two things native memory lacks, plus a set of skills
for managing the resulting store.

- **Sharing**: `/evolve-lite:adapt-memory` mirrors a just-saved native memory
  into the in-repo store at `.evolve/entities/` as a normal Evolve entity,
  which `/evolve-lite:publish` can share like any other (see
  [Publishing Entities](#publishing-entities))
- **Provenance**: `audit_recall.py` records which memories a session actually
  consulted, so their value can be measured over time
- **Git-backed distribution**: `/evolve-lite:subscribe`, `/evolve-lite:publish`,
  `/evolve-lite:sync`, `/evolve-lite:unsubscribe`
- **Store maintenance**: `/evolve-lite:retention` (dry-run by default),
  `/evolve-lite:doctor`, `/evolve-lite:provenance`
- **Session capture**: `/evolve-lite:save`, `/evolve-lite:save-trajectory`,
  `/evolve-lite:synthesize-skill`

This plugin installs **no hooks**. Everything it does is driven by skills and a
single instruction file Claude imports (see below).

## Installation

Run the installer from your project root:

```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform claude
```

or, from a clone of this repo:

```bash
platform-integrations/install.sh install --platform claude --dir /path/to/your/project
```

The installer:

- installs the plugin (`claude plugin marketplace add` +
  `claude plugin install evolve-lite@evolve-marketplace`). Claude loads the
  plugin from the registered marketplace directory at every session start, so
  that directory has to stay. The `curl | bash` form copies the marketplace to
  `~/.claude/evolve-lite/marketplace/` and registers that copy. An install from
  a clone registers the clone itself, so keep the clone where it is;
- copies `EVOLVE.md` to `<project>/.evolve/EVOLVE.md` and adds the import line
  to the project's `CLAUDE.md` (see [How It Works](#how-it-works));
- installs `adapt_memory.py`, `audit_recall.py` and their shared `lib/` under
  `~/.claude/evolve-lite/`;
- allowlists those scripts and `.evolve/` writes in `<project>/.claude/settings.json`
  so they never trigger a per-use permission prompt.

Installing only the plugin — `claude --plugin-dir …` or
`claude plugin install evolve-lite@evolve-marketplace` on its own — loads the
skills but skips the rest, so `EVOLVE.md` never reaches sessions and
`/evolve-lite:doctor` reports `NOT_INSTALLED`. Run the installer as well.

## How It Works

Recall and save stay entirely native — Claude's own judgment, unchanged. The
plugin's contract with Claude lives in one short instruction file. The installer
copies it to `<repo>/.evolve/EVOLVE.md` and injects a single native import line
into the repo's `CLAUDE.md`:

```text
@.evolve/EVOLVE.md
```

> On the first Claude session after install, Claude shows a one-time "allow
> external imports" dialog. You must Allow it, or the import line is silently
> inert and neither lifecycle step below happens.

That file asks Claude for two things:

### After saving a memory — mirror it (sharing)

When Claude saves a native memory, it invokes `/evolve-lite:adapt-memory`, which
writes the same fact into `.evolve/entities/{type}/{name}.md` as a normal Evolve
entity. `{type}` is the native memory's own type (`user`, `feedback`, `project`
or `reference`), and the entity records `source: native-memory` plus the
`native_path` it was mirrored from.

Mirrored memories are publishable like any other entity: `/evolve-lite:publish`
moves them into the shared repo under the same `{type}/` directory (see
[Publishing Entities](#publishing-entities)).

### After consulting memories — log it (provenance)

When Claude reads native memories, it runs:

```bash
python3 ~/.claude/evolve-lite/audit_recall.py <type>/<name> ...
```

recording what was consulted. `/evolve-lite:provenance` later joins those audit
events against saved trajectories to report whether a recalled guideline
actually influenced the session.

Entities are plain markdown under `.evolve/entities/{type}/` — inspect, edit, or
remove them there at any time.

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
  user: yourname          # used to stamp ownership on published entities
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
sync:
  on_session_start: true  # written by subscribe; not acted on yet (see Syncing Repos)
```

- `scope: read` — pulled on sync. Cannot be published to.
- `scope: write` — publish target **and** pulled on sync (so you see
  everything pushed to it, including by other writers).

The `.evolve/` directory is kept out of version control — the skills
automatically add it to `.gitignore`.

### Subscribing to a Repo

Use `/evolve-lite:subscribe` to add either a read-only subscription or a
write-scope publish target:

```text
/evolve-lite:subscribe
> Remote URL: git@github.com:alice/evolve-guidelines.git
> Short name: alice
> Scope: read
```

The repo is cloned into `.evolve/entities/subscribed/alice/`. Repo names must
use only letters, numbers, `.`, `_`, and `-`.

> **Note:** on Claude, recall stays native, and nothing reads
> `.evolve/entities/subscribed/` into a session today. Subscribed guidelines
> are on disk for you to browse, but Claude does not recall them
> automatically.

### Publishing Entities

Use `/evolve-lite:publish` to share one or more of your local entities
(guidelines, or memories mirrored by adapt-memory) via a **write-scope** repo:

1. The skill selects (or asks about) the write-scope target repo
2. It lists files in each `.evolve/entities/{type}/` directory (skipping `subscribed/`)
3. You pick which ones to publish
4. Each selected file is moved into `.evolve/entities/subscribed/{repo}/{type}/`,
   stamped with `visibility: public`, `owner`, `published_at`, and
   `source`, committed, and pushed to the remote

If the same file name exists in more than one type directory, publish refuses
and asks for `--type`, so it never guesses which entity you meant.

Because the publish target is also a subscribed repo, your next sync will
pull in anything other writers have pushed to the same repo.

### Syncing Repos

Use `/evolve-lite:sync` to pull the latest changes from every configured
repo (both scopes):

```text
/evolve-lite:sync
> Synced 2 repo(s): memory [write] (+2 added, 0 updated, 0 removed), bob [read] (+0 added, 1 updated, 0 removed)
```

Sync runs only when you invoke it. This plugin installs no hooks, so
`sync.on_session_start` in the config is not acted on — nothing syncs
automatically at session start.

> **Note:** Read-scope repos use `git fetch` + `git reset --hard`, so the
> local clone always matches the remote exactly (deleted or modified files
> are restored). Write-scope repos use `git fetch` + `git rebase` so any
> unpushed local publish commits are preserved.

### Removing a Repo

Use `/evolve-lite:unsubscribe` to remove any repo and delete its local
clone. The skill shows scope and notes for each configured repo and warns
before removing a write-scope repo (unpushed publishes would be lost).

### Sharing Storage Layout

```text
.evolve/
  entities/
    feedback/                 # mirrored native memories, one dir per native type
      canada-km-only.md
    guideline/                # learned guidelines (also publishable)
      private-guideline.md
    subscribed/
      memory/                 # write-scope clone — publishes land here
        guideline/
          my-published-guideline.md
      alice/                  # read-scope clone
        guideline/
          her-guideline.md
```

## Example Walkthrough

See the [Evolve Lite guide](https://github.com/AgentToolkit/altk-evolve/blob/main/docs/integrations/claude/evolve-lite.md#example-walkthrough) for a step-by-step example showing a memory saved in one session, mirrored into the shared store, and applied in the next.

## Skills Included

There is no `learn` or `recall` skill on Claude — native memory owns both.

### `/evolve-lite:adapt-memory`

Mirror a just-saved native memory into the project's Evolve store
(`.evolve/entities/<native type>/`) so it becomes auditable. Normally invoked
by Claude itself per the EVOLVE.md contract; you can also invoke it by hand.

### `/evolve-lite:doctor`

Diagnose the install — verifies the `CLAUDE.md` `@import` is actually loading
`EVOLVE.md` into sessions (the one failure mode that silently disables
everything). Run this first if Evolve seems inert.

### `/evolve-lite:provenance`

Analyze saved trajectories and recall-audit events offline to record whether
recalled guidelines influenced completed sessions.

### `/evolve-lite:retention`

Apply data-retention rules to the local store — flag or delete stale and unused
memories and expired sessions. Dry-run by default.

### `/evolve-lite:synthesize-skill`

Convert a saved trajectory into a reusable skill (SKILL.md plus supporting
scripts), promoting a workflow from free-text guidance to something executable.

### `/evolve-lite:save`

Manually invoke to capture successful workflows from your current session and save them as reusable skills:
- Analyzes conversation history (user requests, reasoning, tool calls, responses)
- Generates parameterized SKILL.md documentation
- Creates Python helper scripts for programmatic operations (when applicable)
- Saves to `~/.claude/skills/{skill-name}/` for cross-project availability

**Quick Start:**
```
User: [Complete a successful task]
User: "save"
Assistant: "What would you like to name this skill?"
User: "my-workflow-name"
```

### `/evolve-lite:save-trajectory`

Manually invoke to export the current conversation as a trajectory JSON file:
- Converts all messages to OpenAI chat completion format (user, assistant, tool calls, tool results)
- Strips system reminders and cleans content
- Saves to `.evolve/trajectories/` with a timestamped filename
- Useful for trajectory analysis, fine-tuning data collection, and session review
- Runs in a forked context to keep the parent conversation clean

## Entities Storage

Entities are stored as individual markdown files in `.evolve/entities/`, nested
by type. A mirrored native memory lands under its native type:

```
.evolve/entities/
  feedback/
    canada-km-only.md
  project/
    use-pillow-for-image-metadata.md
```

Each file uses markdown with YAML frontmatter:

```markdown
---
type: project
trigger: When extracting image metadata in a sandbox without exiftool
source: native-memory
native_path: /Users/you/.claude/projects/<hash>/memory/use-pillow-for-image-metadata.md
---

Use Python Pillow for image metadata extraction in sandboxed environments.

System tools like exiftool may not be installed in the sandbox.
```

## Environment Variables

- `EVOLVE_DEBUG`: set to any non-empty value to append debug lines from the
  helper scripts to `$TMPDIR/evolve-<uid>/evolve-plugin.log`.
- `EVOLVE_DIR`: **not supported end to end yet.** Some scripts read it, but
  `EVOLVE.md` and the installer always use `<project>/.evolve`, and
  `subscribe` and `sync` resolve the project root from it differently. Leave
  it unset.

## Verification

After installation:

1. `claude plugin list` confirms the plugin is enabled.
2. `/evolve-lite:doctor` confirms `EVOLVE.md` is actually reaching sessions —
   the import dialog above is easy to miss, and nothing else reports it.

## Plugin Structure

```text
evolve-lite/
├── .claude-plugin/
│   └── plugin.json                  # Plugin manifest (its `skills` key points
│                                    # at ./skills/evolve-lite/)
├── EVOLVE.md                        # The two-step contract, imported by CLAUDE.md
├── lib/
│   └── evolve-lite/                 # Shared helpers used by the skill scripts
│       ├── audit_recall.py          # Also installed to ~/.claude/evolve-lite/
│       ├── audit.py
│       ├── config.py
│       ├── entity_io.py
│       └── retention.py
├── skills/
│   └── evolve-lite/                 # Namespace dir → skills are `evolve-lite:<name>`
│       ├── adapt-memory/
│       ├── doctor/
│       ├── provenance/
│       ├── publish/
│       ├── retention/
│       ├── save/
│       ├── save-trajectory/
│       ├── subscribe/
│       ├── sync/
│       ├── synthesize-skill/
│       └── unsubscribe/             # each: SKILL.md [+ scripts/]
└── README.md
```

No `hooks/` directory — this plugin ships no hooks.

### Updating or withdrawing shared memories

Use `evolve-lite:publish --update` to update an owned publication and
`evolve-lite:unpublish` to restore it to private storage. Supply `--entity` and,
when needed, `--type` and `--repo`. Update and unpublish require a matching
owner; when `identity.user` is configured, `--user` must agree with it.
Unpublish refuses to overwrite an existing private copy. If removing the shared
copy fails, it rolls back its unchanged private restoration so the operation
can be retried. Commit and push the deletion in the shared clone to withdraw
the remote copy; existing subscribers may retain copies until they sync.
