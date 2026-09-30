# Evolve Lite

Evolve Lite is a lightweight mode that runs as a Claude Code plugin — no vector store, no MCP servers, no API keys required. It stores entities as Markdown files with YAML frontmatter under `.evolve/entities/` in your project directory, and rides on Claude Code's own native memory rather than installing hooks of its own.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed with credentials configured

## Installation

### From Local Directory

```bash
claude --plugin-dir /path/to/altk-evolve/platform-integrations/claude/plugins/evolve-lite
```

### From Marketplace

```bash
claude plugin marketplace add AgentToolkit/altk-evolve
claude plugin install evolve-lite@evolve-marketplace
```

Verify the plugin is active:

```bash
claude plugin list
```

## How It Works

Claude Code already recalls and saves memories on its own judgment: it decides what to look up at the start of a task and what to write down at the end. Evolve Lite does not intercept either. It adds the two things native memory lacks:

1. **Sharing** — When Claude saves a native memory, it invokes `/evolve-lite:adapt-memory`, which mirrors the same fact into `.evolve/entities/{type}/{name}.md` as a normal Evolve entity. From there it can be published to a git repo and pulled by a teammate.

2. **Provenance** — When Claude consults native memories, it runs `python3 ~/.claude/evolve-lite/audit_recall.py <type>/<name>`, recording what was actually opened so the store's value can be measured over time.

Both steps come from one short instruction file. The installer copies it to `<repo>/.evolve/EVOLVE.md` and injects a single native import line, `@.evolve/EVOLVE.md`, into the repo's `CLAUDE.md`. Claude shows a one-time "allow external imports" dialog on the first session afterwards — you must Allow it, or the import is inert and neither step happens. Run `/evolve-lite:doctor` to check.

No external services and no hooks are involved. The whole mechanism is one imported Markdown file, a directory of entity files, and a handful of Python scripts.

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

        Mirrored 1 memory into the shared evolve store.
        Entities stored in: /path/to/project/.evolve/entities
```

Claude decided to save that on its own — you did not invoke a skill. The `adapt-memory` step is what makes the saved fact shareable: it now exists as `.evolve/entities/strategy/....md`, ready for `/evolve-lite:publish`.

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

This example shows how Evolve Lite helps an agent avoid dead ends it encountered in a previous session. The agent runs in a Docker sandbox where common system tools are not installed.

To run it, first build the Docker image:

```bash
just sandbox-build target=claude
```

Then run a task to extract metadata from an image:

```bash
just claude-prompt 'where was the photo @sample.jpg taken. use exif metadata'
```

When you run the above, you will see that the agent hits some dead ends (exiftool not found, Pillow not installed), and that it saves memories to avoid them in the future (mirrored into `demo/workspace/.evolve/entities/`).

Run the same task again in a new session:

```bash
just claude-prompt 'where was the photo @sample.jpg taken. use exif metadata'
```

You will see that the agent now is more efficient, avoiding the dead ends it encountered in the previous session such as trying to use exiftool.

The instructions below walk you through the same example in interactive sessions.


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

        Mirrored 2 memories into the shared evolve store.
        Entities stored in: /workspace/.evolve/entities
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
| `/evolve-lite:adapt-memory` | Mirror a just-saved native memory into the shared store |
| `/evolve-lite:doctor` | Verify the `CLAUDE.md` import is actually loading `EVOLVE.md` |
| `/evolve-lite:subscribe` | Add a shared guidelines repo (read-scope or write-scope) |
| `/evolve-lite:publish` | Publish a local guideline to a write-scope repo |
| `/evolve-lite:sync` | Pull the latest guidelines from every configured repo |
| `/evolve-lite:unsubscribe` | Remove a repo and delete its local clone |
| `/evolve-lite:provenance` | Report whether recalled guidelines influenced past sessions |
| `/evolve-lite:retention` | Flag or delete stale entities and expired sessions (dry-run by default) |
| `/evolve-lite:save` | Capture a successful workflow as a reusable skill |
| `/evolve-lite:save-trajectory` | Export the conversation as a trajectory JSON file |
| `/evolve-lite:synthesize-skill` | Turn a saved trajectory into an executable skill |

## Entities Storage

Entities live in `.evolve/entities/` in the project root, organized into type-based subdirectories:

```text
.evolve/entities/
  strategy/
    use-python-stdlib-struct-module-to-manually-parse-jpeg-exif-gps.md
```

Each entity file uses Markdown with YAML frontmatter:

```markdown
---
type: strategy
trigger: When extracting EXIF or GPS metadata from images in containerized or sandboxed environments
---

Use Python stdlib (struct module) to manually parse JPEG EXIF/GPS metadata when no external tools are available

## Rationale

In sandboxed environments, neither system tools (exiftool) nor third-party libraries (PIL/Pillow) may be installed. Python stdlib is always available.
```

Override the storage location with the `EVOLVE_DIR` environment variable, which moves the whole `.evolve/` directory (entities, trajectories, config).

## Tradeoffs

Lite mode is easier to set up:

- No vector DB
- No MCP servers
- No need to access agent logs or emit events to an observability tool
- No need to specify an LLM API key

But it has a number of limitations:

- **Inefficient context usage** — Saving and recalling both happen inside the agent's context window, not in a separate process. Full Evolve offloads all processing to the MCP server, keeping the agent's context free for the actual task.
- **Scalability** — Recall is whatever the host's native memory decides to open; there is no semantic search over the store, so guidelines pulled in from subscribed repos are not ranked by relevance to the task. Full Evolve retrieves only the relevant subset, which scales to large entity sets.
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
