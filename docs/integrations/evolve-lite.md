# Evolve Lite

Evolve Lite is a lightweight mode that runs as a Claude Code plugin — no vector store, no MCP servers, no API keys required. It stores entities as Markdown files with YAML frontmatter under `.evolve/entities/` in your project directory and uses Claude Code's built-in hooks to inject them automatically.

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
claude plugin install evolve@evolve-marketplace
```

Verify the plugin is active:

```bash
claude plugin list
```

## How It Works

Evolve Lite has two halves:

1. **Learn** — You invoke `/evolve:learn` at the end of a conversation. The plugin analyzes what happened, produces an entity JSON payload, and saves each entity as a Markdown file under `.evolve/entities/`.

2. **Recall** — On every subsequent prompt, a `UserPromptSubmit` hook automatically loads stored entities and injects them into the conversation context. Claude applies whichever entities are relevant to the current task.

No external services are involved. The entire loop is a directory of entity files and two Python scripts.

## Example Walkthrough

The examples below show the learn-then-recall loop across multiple sessions, starting with a simple preference and then a more complex recovery pattern.

## Example 1 — Learning a user preference

This example shows the full learn-then-recall loop across two sessions.

### Session 1 — Learn a preference

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

You: /evolve:learn

Claude: I've extracted the following entities from this conversation:

        {
          "entities": [
            {
              "content": "When presenting distances between Canadian cities, use kilometres only — do not include miles",
              "rationale": "User explicitly prefers metric units for Canadian geography",
              "category": "strategy",
              "trigger": "When discussing distances or travel between cities in Canada"
            }
          ]
        }

        Added 1 new entity(ies). Total: 1
        Entities stored in: /path/to/project/.evolve/entities
```

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

The entity was automatically injected via the `UserPromptSubmit` hook, so Claude used kilometres only — without being reminded.

## Example 2 — Learning from failure in a sandboxed environment

This example shows how Evolve Lite helps an agent avoid dead ends it encountered in a previous session. The agent runs in a Docker sandbox where common system tools are not installed.

To run it, first build the Docker image:

```bash
just sandbox-build
```

Then run a task to extract metadata from an image:

```bash
just trace=true learn=true sandbox-prompt 'where was the photo @sample.jpg taken. use exif metadata'
```

When you run the above, you will see that the agent hits some dead ends (exiftool not found, Pillow not installed), and that it learns guidelines to avoid them in the future (see `demo/workspace/.evolve/entities/`).

Run the same task again in a new session:

```bash
just trace=true sandbox-prompt 'where was the photo @sample.jpg taken. use exif metadata'
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

The agent got the right answer, but wasted two turns on dead ends (exiftool not found, Pillow not installed). Now the user runs `/evolve:learn`:

```text
You: /evolve:learn

Claude: I've extracted the following entities from this conversation:

        {
          "entities": [
            {
              "content": "Use Python stdlib (struct module) to manually parse JPEG
                          EXIF/GPS metadata when no external tools are available",
              "rationale": "In sandboxed environments, neither system tools (exiftool)
                          nor third-party libraries (PIL/Pillow) may be installed.
                          Python stdlib is always available and sufficient for JPEG
                          binary parsing.",
              "category": "strategy",
              "trigger": "When extracting EXIF or GPS metadata from images in
                          containerized or sandboxed environments"
            },
            {
              "content": "When system tools or pip packages are unavailable, check if
                          the task can be solved with Python stdlib before attempting
                          installs",
              "rationale": "Binary parsing, HTTP requests (urllib), JSON, CSV, and many
                          common tasks are fully covered by stdlib. Attempting installs
                          in sandboxes wastes time and often fails silently.",
              "category": "recovery",
              "trigger": "When a required CLI tool or Python package is missing in a
                          restricted environment"
            }
          ]
        }

        Added 2 new entity(ies). Total: 2
        Entities stored in: /workspace/.evolve/entities
```

### Session 2 — No more dead ends

In a new session, the user asks the same question. The stored entities are automatically injected via the `UserPromptSubmit` hook:

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

| Skill | Description |
|-------|-------------|
| `/evolve:learn` | Extract entities from the current conversation and save them |
| `/evolve:recall` | Manually retrieve and display stored entities |
| `/evolve:save` | Capture a successful workflow as a reusable skill |

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

Override the storage location with the `EVOLVE_ENTITIES_DIR` environment variable.

## Tradeoffs

Lite mode is easier to set up:

- No vector DB
- No MCP servers
- No need to access agent logs or emit events to an observability tool
- No need to specify an LLM API key

But it has a number of limitations:

- **Inefficient context usage** — Entity extraction and recall both happen inside the agent's context window, not in a separate process. Full Evolve offloads all processing to the MCP server, keeping the agent's context free for the actual task.
- **Scalability** — All entities are injected on every prompt. Full Evolve uses semantic search to retrieve only the relevant subset, which scales to large entity sets.
- **Single-trajectory visibility** — Lite mode only extracts entities from the current session. Full Evolve can ingest complete trajectories across multiple sessions and glean insights that a single-conversation view would miss.
- **Entity consolidation** — Lite mode simply appends new entities. Full Evolve performs LLM-based conflict resolution to merge, supersede, or refine entities, and garbage-collects stale ones.

| Capability | Evolve Lite | Full Evolve |
|------------|-------------|-------------|
| Entity storage | Markdown files in `.evolve/entities/` | Milvus vector store |
| Retrieval | All entities injected via hooks | Semantic search via MCP |
| Conflict resolution | Append-only | LLM-based merging + garbage collection |
| Trajectory analysis | Current session only (`/evolve:learn`) | Multi-session, automatic via MCP |
| Context efficiency | Consumes main agent context | Processes separately via MCP |
| Observability | Not required | Ingests from agent logs / trace events |
| Infrastructure | None | MCP server + vector DB + API key |
| Setup time | < 1 minute | ~10 minutes |
