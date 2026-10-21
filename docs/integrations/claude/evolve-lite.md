# Evolve Lite

Evolve Lite is a lightweight mode that runs as a Claude Code plugin — no vector store, no MCP servers, no API keys required. It stores entities as Markdown files with YAML frontmatter under `.evolve/entities/` in your project directory, and rides on Claude Code's own native memory rather than installing hooks of its own.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed with credentials configured

## Installation

Run the installer from your project root:

```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform claude
```

or, from a clone of the repo:

```bash
platform-integrations/install.sh install --platform claude --dir /path/to/your/project
```

The installer installs the plugin from the marketplace (`evolve-lite@evolve-marketplace`). Claude loads the plugin from the registered marketplace directory at every session start, so that directory has to stay: the `curl | bash` form copies the marketplace to `~/.claude/evolve-lite/marketplace/` and registers that copy, while an install from a clone registers the clone itself, so keep the clone where it is. The installer also copies `EVOLVE.md` into `<project>/.evolve/`, adds its import line to the project's `CLAUDE.md`, installs `adapt_memory.py`, `audit_recall.py` and their shared `lib/` under `~/.claude/evolve-lite/`, and allowlists those scripts in `<project>/.claude/settings.json`.

Installing only the plugin (`claude --plugin-dir …`, or `claude plugin install` on its own) loads the skills but skips the rest, so nothing reaches your sessions. Run the installer as well.

Verify the install:

```bash
claude plugin list        # the plugin is enabled
```

Then run `/evolve-lite:doctor` in a Claude session to confirm `EVOLVE.md` is being loaded.

## How It Works

Claude Code already recalls and saves memories on its own judgment: it decides what to look up at the start of a task and what to write down at the end. Evolve Lite does not intercept either. It adds the two things native memory lacks:

1. **Sharing** — When Claude saves a native memory, it invokes `/evolve-lite:adapt-memory`, which mirrors the same fact into `.evolve/entities/{type}/{name}.md` as a normal Evolve entity, where `{type}` is the native memory's type (`user`, `feedback`, `project` or `reference`). Mirrored memories can be shared with `/evolve-lite:publish`, which keeps the `{type}/` directory in the shared repo.

2. **Provenance** — When Claude consults native memories, it runs `python3 ~/.claude/evolve-lite/audit_recall.py <type>/<name>`, recording what was actually opened so the store's value can be measured over time.

Both steps come from one short instruction file. The installer copies it to `<repo>/.evolve/EVOLVE.md` and injects a single native import line, `@.evolve/EVOLVE.md`, into the repo's `CLAUDE.md`. Claude shows a one-time "allow external imports" dialog on the first session afterwards — you must Allow it, or the import is inert and neither step happens. Run `/evolve-lite:doctor` to check.

No external services and no hooks are involved. Beyond the plugin itself, the whole mechanism is one imported Markdown file, a directory of entity files, a few Python scripts under `~/.claude/evolve-lite/`, and the allow-rules in `.claude/settings.json`.

## Example Walkthrough

The examples below show a memory saved in one session being applied in the next, starting with a simple preference and then a more complex recovery pattern.

## Example 1 — Learning a user preference

### Session 1 — Claude saves a preference

```text
You: Tell me the distances between the 3 most populous cities in Canada.

Claude: The three most populous cities in Canada are Toronto, Montreal, and
        Vancouver. Here are the distances between them:

        | City Pair                | Distance (km) | Distance (miles) |
        |--------------------------|---------------|------------------|
        | Toronto ↔ Montreal       | 541           | 336              |
        | Toronto ↔ Vancouver      | 4,338         | 2,695            |
        | Montreal ↔ Vancouver     | 4,810         | 2,989            |

You: For cities in Canada use km only.

Claude: Got it — I'll use kilometres only for Canadian cities.

        [saves a native memory: "When presenting distances between Canadian
         cities, use kilometres only — do not include miles"]

        [invokes evolve-lite:adapt-memory]

        Mirrored native memory into evolve store: /path/to/project/.evolve/entities/feedback/canada-km-only.md
        Entity id: feedback/canada-km-only
```

Claude decided to save that on its own — you did not invoke a skill. The `adapt-memory` step mirrors the saved fact into the project's evolve store as `.evolve/entities/feedback/canada-km-only.md`, where it is auditable like any other entity. It can be shared with `/evolve-lite:publish`, which keeps it under `feedback/` in the shared repo.

### Session 2 — Preference is applied automatically

Start a new Claude Code session in the same project:

```text
You: Tell me the distances between the 3 most populous cities in Canada.

Claude: The three most populous cities in Canada are Toronto, Montreal, and
        Vancouver. Here are the distances between them:

        | City Pair                | Distance (km) |
        |--------------------------|---------------|
        | Toronto ↔ Montreal       | 541           |
        | Toronto ↔ Vancouver      | 4,338         |
        | Montreal ↔ Vancouver     | 4,810         |
```

Claude recalled the memory itself and used kilometres only — without being reminded.

## Example 2 — Learning from failure in a sandboxed environment

This example shows how Evolve Lite helps an agent avoid dead ends it encountered in a previous session. It assumes an environment where common tools — `exiftool` and the Pillow library — are not installed.

To reproduce it, install Evolve Lite into a project as described in [Installation](#installation), copy [`demo/workspace/sample.jpg`](https://github.com/AgentToolkit/altk-evolve/blob/main/demo/workspace/sample.jpg) into that project, and run the two sessions below as two separate interactive Claude sessions in the same project. Native memory persists between them because both sessions share the same `~/.claude/projects/<hash>/memory/` store.

### Session 1 — Solving the hard way

The user asks the agent to find where a photo was taken:

```text
You: Where was this photo taken? [sample.jpg]

Claude: I'll extract the GPS coordinates from the photo's EXIF metadata.

        [runs: exiftool sample.jpg]

        exiftool: command not found

        Let me try using Python with Pillow instead.

        [runs: python3 -c "from PIL import Image; ..."]

        ModuleNotFoundError: No module named 'PIL'

        I'll install Pillow first.

        [runs: pip install Pillow]
        [runs: python3 script to extract EXIF GPS via Pillow]

        The photo was taken at:
        - Latitude:  51.4936° N
        - Longitude: 0.0376° E

        This places it in Wapping, East London, UK.
        Google Maps: https://www.google.com/maps?q=51.493608,0.037561
```

The agent got the right answer, but wasted two turns on dead ends (exiftool not found, Pillow not installed). Having learned something worth keeping, it saves memories and mirrors them:

```text
Claude: [saves a native memory: "Use Python stdlib (struct module) to manually
         parse JPEG EXIF/GPS metadata when no external tools are available" —
         in sandboxed environments neither system tools (exiftool) nor
         third-party libraries (PIL/Pillow) may be installed]

        [saves a native memory: "When system tools or pip packages are
         unavailable, check if the task can be solved with Python stdlib
         before attempting installs"]

        [invokes evolve-lite:adapt-memory for each]

        Mirrored native memory into evolve store: /path/to/project/.evolve/entities/project/parse-exif-with-python-stdlib.md
        Entity id: project/parse-exif-with-python-stdlib
        Mirrored native memory into evolve store: /path/to/project/.evolve/entities/project/prefer-stdlib-before-installs.md
        Entity id: project/prefer-stdlib-before-installs
```

### Session 2 — No more dead ends

In a new session, the user asks the same question. Claude recalls the memories it saved:

```text
You: Where was this photo taken? [sample.jpg]

Claude: I'll extract the GPS coordinates from the EXIF data using Python's
        struct module.

        [runs: python3 script using struct to parse JPEG EXIF binary data]

        The photo was taken at:
        - Latitude:  51.4936° N
        - Longitude: 0.0376° E

        This places it in Wapping, East London, UK.
        Google Maps: https://www.google.com/maps?q=51.493608,0.037561
```

The agent skipped both `exiftool` and `Pillow` entirely — it went straight to the Python stdlib approach that it learned works in sandboxed environments. Two wasted turns eliminated.

## Available Skills

There is no `learn` or `recall` skill on Claude — native memory owns both.

| Skill | Description |
|-------|-------------|
| `/evolve-lite:adapt-memory` | Mirror a just-saved native memory into the project's evolve store |
| `/evolve-lite:doctor` | Verify the `CLAUDE.md` import is actually loading `EVOLVE.md` |
| `/evolve-lite:subscribe` | Add a shared guidelines repo (read-scope or write-scope) |
| `/evolve-lite:publish` | Publish a local entity (guideline or mirrored memory) to a write-scope repo |
| `/evolve-lite:sync` | Pull the latest guidelines from every configured repo |
| `/evolve-lite:unsubscribe` | Remove a repo and delete its local clone |
| `/evolve-lite:provenance` | Report whether recalled guidelines influenced past sessions |
| `/evolve-lite:retention` | Flag or delete stale entities and expired sessions (dry-run by default) |
| `/evolve-lite:save` | Capture a successful workflow as a reusable skill |
| `/evolve-lite:save-trajectory` | Export the conversation as a trajectory JSON file |
| `/evolve-lite:synthesize-skill` | Turn a saved trajectory into an executable skill |

## Entities Storage

Entities live in `.evolve/entities/` in the project root, organized into type-based subdirectories. A mirrored native memory lands under its native type (`user`, `feedback`, `project` or `reference`):

```text
.evolve/entities/
  feedback/
    canada-km-only.md
  project/
    parse-exif-with-python-stdlib.md
```

Each entity file uses Markdown with YAML frontmatter:

```markdown
---
type: project
trigger: When extracting EXIF or GPS metadata from images in containerized or sandboxed environments
source: native-memory
native_path: /Users/you/.claude/projects/<hash>/memory/parse-exif-with-python-stdlib.md
---

Use Python stdlib (struct module) to manually parse JPEG EXIF/GPS metadata when no external tools are available.

In sandboxed environments, neither system tools (exiftool) nor third-party libraries (PIL/Pillow) may be installed. Python stdlib is always available.
```

The store always lives at `<project>/.evolve/`. Some helper scripts read an `EVOLVE_DIR` environment variable, but it is not supported end to end yet — `EVOLVE.md` and the installer always use `.evolve/`, and `subscribe` and `sync` resolve the project root from it differently — so leave it unset.

## Tradeoffs

Lite mode is easier to set up:

- No vector DB
- No MCP servers
- No need to access agent logs or emit events to an observability tool
- No need to specify an LLM API key

But it has a number of limitations:

- **Inefficient context usage** — Saving and recalling both happen inside the agent's context window, not in a separate process. Full Evolve offloads all processing to the MCP server, keeping the agent's context free for the actual task.
- **Scalability** — Recall is whatever the host's native memory decides to open; there is no semantic search over the store. Guidelines pulled in from subscribed repos land in `.evolve/entities/subscribed/`, but nothing on Claude reads that directory into a session, so they are not recalled at all today. Full Evolve retrieves only the relevant subset, which scales to large entity sets.
- **Single-trajectory visibility** — Lite mode only ever captures what the current session learned. Full Evolve can ingest complete trajectories across multiple sessions and glean insights that a single-conversation view would miss.
- **Entity consolidation** — Lite mode simply appends new entities. Full Evolve performs LLM-based conflict resolution to merge, supersede, or refine entities, and garbage-collects stale ones.

| Capability | Evolve Lite | Full Evolve |
|------------|-------------|-------------|
| Entity storage | Markdown files in `.evolve/entities/` | Milvus vector store |
| Retrieval | Claude's native memory (no semantic search) | Semantic search via MCP |
| Conflict resolution | Append-only | LLM-based merging + garbage collection |
| Trajectory analysis | Current session only | Multi-session, automatic via MCP |
| Context efficiency | Consumes main agent context | Processes separately via MCP |
| Observability | Not required | Ingests from agent logs / trace events |
| Infrastructure | None | MCP server + vector DB + API key |
| Setup time | < 1 minute | ~10 minutes |

### Updating or withdrawing shared memories

Use `evolve-lite:publish --update` to update an owned publication and
`evolve-lite:unpublish` to restore it to private storage. Supply `--entity` and,
when needed, `--type` and `--repo`. Update and unpublish require a matching
owner; when `identity.user` is configured, `--user` must agree with it.
Unpublish refuses to overwrite an existing private copy. If removing the shared
copy fails, it rolls back its unchanged private restoration so the operation
can be retried. Commit and push the deletion in the shared clone to withdraw
the remote copy; existing subscribers may retain copies until they sync.
