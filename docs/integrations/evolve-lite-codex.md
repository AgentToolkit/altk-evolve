# Evolve Lite — Codex Integration

Evolve Lite is a lightweight mode that runs as a Codex plugin. It stores entities as Markdown files with YAML frontmatter under `.evolve/entities/` in your project directory and uses a Codex `UserPromptSubmit` hook to inject them automatically. No vector store, MCP server, or separate Evolve service is required.

## Prerequisites

- [Codex CLI](https://github.com/openai/codex) installed with authentication configured
- Docker only if you want to run the sandbox demo workflow

## Installation

### From the repository root

```bash
./platform-integrations/install.sh install --platform codex --dir /path/to/project
```

This installs:

- `plugins/evolve-lite/`
- `.agents/plugins/marketplace.json`
- `.codex/hooks.json`

Enable automatic recall in `~/.codex/config.toml`:

```toml
[features]
codex_hooks = true
```

For a normal local Codex install, that is the config file in your host home directory.
For the sandbox demo in this repo, the container bootstrap writes the needed Codex settings into the container's Codex home for that run. If you choose to mount your host `~/.codex` to `/codex-home`, those settings are written into that mounted home instead.

If you do not want to enable Codex hooks, you can still invoke `evolve-lite:recall` manually when you want the saved guidance loaded into the current session.

### In the demo workspace

Start Codex in `demo/workspace`, then install the local plugin:

```bash
just codex-run
# inside the container:
codex
```

Then:

1. Run `/plugins`
2. Open `Evolve Local Plugins`
3. Install `evolve`
4. Start a new thread in `/workspace`

The demo plugin manifest lives at `demo/workspace/plugins/evolve-lite/.codex-plugin/plugin.json`.

## How It Works

Evolve Lite has two halves:

1. **Learn** — You invoke `evolve-lite:learn` after a Codex session. The plugin analyzes what happened and saves entities as Markdown files under `.evolve/entities/`.

2. **Recall** — On every subsequent prompt, a `UserPromptSubmit` hook automatically loads stored entities and injects them into the conversation context. Codex applies whichever entities are relevant to the current task.

No external services are involved. The entire loop is a directory of entity files and two helper scripts.

The stored entities live in the workspace under `.evolve/entities/`, so the learn and recall loop stays entirely file-backed.

## Example Walkthrough

The examples below show the learn-then-recall loop across multiple Codex sessions, starting with a simple preference and then a sandbox recovery pattern.

## Example 1 — Learning a user preference

This example shows the full learn-then-recall loop across two sessions.

### Session 1 — Learn a preference

```text
You: Tell me the distances between the 3 most populous cities in Canada.

Codex: The three most populous cities in Canada are Toronto, Montreal, and
       Vancouver. Here are the distances between them:

       | City Pair                | Distance (km) | Distance (miles) |
       |--------------------------|---------------|------------------|
       | Toronto ↔ Montreal       | 541           | 336              |
       | Toronto ↔ Vancouver      | 4,338         | 2,695            |
       | Montreal ↔ Vancouver     | 4,810         | 2,989            |

You: For cities in Canada use km only.

Codex: I'll use kilometres only for Canadian cities.

You: Save new Evolve learnings from this session.

Codex: I'll run `evolve-lite:learn` and save any reusable guidance from this
       session.

       {
         "entities": [
           {
             "content": "When presenting distances between Canadian cities, use kilometres only and do not include miles",
             "rationale": "User explicitly prefers metric units for Canadian geography",
             "type": "guideline",
             "trigger": "When discussing distances or travel between cities in Canada"
           }
         ]
       }

       Added 1 new entity(ies). Total: 1
       Entities stored in: /path/to/project/.evolve/entities
```

### Session 2 — Preference is applied automatically

Start a new Codex session in the same project:

```text
You: Tell me the distances between the 3 most populous cities in Canada.

Codex: The three most populous cities in Canada are Toronto, Montreal, and
       Vancouver. Here are the distances between them:

       | City Pair                | Distance (km) |
       |--------------------------|---------------|
       | Toronto ↔ Montreal       | 541           |
       | Toronto ↔ Vancouver      | 4,338         |
       | Montreal ↔ Vancouver     | 4,810         |
```

The entity was automatically injected via the `UserPromptSubmit` hook, so Codex used kilometres only without being reminded.

## Example 2 — Learning from failure in a sandboxed environment

This example shows how Evolve Lite helps Codex avoid dead ends it encountered in a previous sandbox session.

Build and start the Codex sandbox:

```bash
just sandbox-build codex
just codex-run
```

Inside the container:

```bash
codex
```

### Session 1 — Solving the hard way

```text
You: Where was this photo taken? [sample.jpg]

Codex: I'll extract the GPS coordinates from the photo's EXIF metadata.

       [runs: exiftool sample.jpg]

       exiftool: command not found

       Let me try Python with Pillow instead.

       [runs: python3 -c "from PIL import Image; ..."]

       ModuleNotFoundError: No module named 'PIL'

       I'll parse the JPEG EXIF data with Python's struct module instead.

       [runs: python3 script using struct to parse JPEG EXIF binary data]

       The photo was taken at:
       - Latitude:  51.4936° N
       - Longitude: 0.0376° E

       This places it in Wapping, East London, UK.
       Google Maps: https://www.google.com/maps?q=51.493608,0.037561
```

The agent got the right answer, but wasted time on two dead ends. Now the user saves the learning:

```text
You: Save new Evolve learnings from this session.

Codex: I'll run `evolve-lite:learn` and save the most useful guidelines from
       this session.

       {
         "entities": [
           {
             "content": "Use Python stdlib (struct module) to manually parse JPEG EXIF/GPS metadata when no external tools are available",
             "rationale": "In sandboxed environments, system tools and third-party packages may be unavailable, but Python stdlib is sufficient for JPEG binary parsing",
             "type": "guideline",
             "trigger": "When extracting EXIF or GPS metadata from images in containerized or sandboxed environments"
           },
           {
             "content": "When system tools or pip packages are unavailable, check whether Python stdlib can solve the task before attempting installs",
             "rationale": "Common tasks such as binary parsing, HTTP requests, JSON, and CSV processing are often fully covered by the standard library",
             "type": "guideline",
             "trigger": "When a required CLI tool or Python package is missing in a restricted environment"
           }
         ]
       }

       Added 2 new entity(ies). Total: 2
       Entities stored in: /workspace/.evolve/entities
```

### Session 2 — No more dead ends

In a new session, the user asks the same question:

```text
You: Where was this photo taken? [sample.jpg]

Codex: I'll extract the GPS coordinates from the EXIF data using Python's
       struct module.

       [runs: python3 script using struct to parse JPEG EXIF binary data]

       The photo was taken at:
       - Latitude:  51.4936° N
       - Longitude: 0.0376° E

       This places it in Wapping, East London, UK.
       Google Maps: https://www.google.com/maps?q=51.493608,0.037561
```

Codex skipped both dead ends and went straight to the approach it had already learned works in the sandbox.

## Available Skills

| Skill | Description |
|-------|-------------|
| `evolve-lite:learn` | Extract entities from the current session and save them |
| `evolve-lite:recall` | Manually retrieve and display stored entities |

## Entities Storage

Entities live in `.evolve/entities/` in the project root, organized into type-based subdirectories:

```text
.evolve/entities/
  guideline/
    use-python-stdlib-struct-module-to-manually-parse-jpeg-exif-gps.md
```

Each entity file uses Markdown with YAML frontmatter:

```markdown
---
type: guideline
trigger: When extracting EXIF or GPS metadata from images in containerized or sandboxed environments
---

Use Python stdlib (struct module) to manually parse JPEG EXIF/GPS metadata when no external tools are available

## Rationale

In sandboxed environments, system tools and third-party packages may be unavailable, but Python stdlib is sufficient for JPEG binary parsing.
```

Override the storage location with the `EVOLVE_DIR` environment variable.

## Tradeoffs

Lite mode is easier to set up:

- No vector DB
- No MCP servers
- No need to ingest agent logs or trace events
- No separate Evolve service to run

But it has a number of limitations:

- **Inefficient context usage** — Entity extraction and recall both happen inside the main agent flow instead of a separate service.
- **Scalability** — Stored entities are injected through hooks rather than retrieved through semantic search, so the approach is best for smaller entity sets.
- **Single-trajectory visibility** — Lite mode only extracts entities from the current session. Full Evolve can learn across multiple sessions.
- **Entity consolidation** — Lite mode appends new entities. Full Evolve performs conflict resolution and cleanup.

| Capability | Evolve Lite | Full Evolve |
|------------|-------------|-------------|
| Entity storage | Markdown files in `.evolve/entities/` | Milvus vector store |
| Retrieval | Injected via Codex hooks | Semantic search via MCP |
| Conflict resolution | Append-only | LLM-based merging + garbage collection |
| Trajectory analysis | Current session only (`evolve-lite:learn`) | Multi-session, automatic via MCP |
| Context efficiency | Consumes main agent context | Processes separately via MCP |
| Observability | Not required | Ingests from agent logs or trace events |
| Infrastructure | None | MCP server + vector DB + API key |
| Setup time | < 1 minute | ~10 minutes |
