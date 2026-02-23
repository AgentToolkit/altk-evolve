# Kaizen Lite

Kaizen Lite is a lightweight mode that runs as a Claude Code plugin — no vector store, no MCP servers, no API keys required. It stores entities as plain JSON in your project directory and uses Claude Code's built-in hooks to inject them automatically.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed with credentials configured

## Installation

### From Local Directory

```bash
claude --plugin-dir /path/to/kaizen/repo/plugins/kaizen
```

### From Marketplace

```bash
claude plugin marketplace add AgentToolkit/kaizen
claude plugin install kaizen@kaizen-marketplace
```

Verify the plugin is active:

```bash
claude plugin list
```

## How It Works

Kaizen Lite has two halves:

1. **Learn** — You invoke `/kaizen:learn` at the end of a conversation. The plugin analyzes what happened and extracts reusable entities (preferences, strategies, recovery patterns) into `.kaizen/entities.json`.

2. **Recall** — On every subsequent prompt, a `UserPromptSubmit` hook automatically loads stored entities and injects them into the conversation context. Claude applies whichever entities are relevant to the current task.

No external services are involved. The entire loop is a JSON file and two Python scripts.

## Example Walkthrough

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

You: /kaizen:learn

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
        Entities stored in: /path/to/project/.kaizen/entities.json
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

## Available Skills

| Skill | Description |
|-------|-------------|
| `/kaizen:learn` | Extract entities from the current conversation and save them |
| `/kaizen:recall` | Manually retrieve and display stored entities |
| `/kaizen:save` | Capture a successful workflow as a reusable skill |

## Entities Storage

Entities live in `.kaizen/entities.json` in the project root:

```json
{
  "entities": [
    {
      "content": "Use Python PIL/Pillow for image metadata extraction in sandboxed environments",
      "rationale": "System tools like exiftool may not be available",
      "category": "strategy",
      "trigger": "When extracting image metadata in containerized environments"
    }
  ]
}
```

Override the storage location with the `KAIZEN_ENTITIES_FILE` environment variable.

## Tradeoffs

Lite mode is easier to set up:

- No vector DB
- No MCP servers
- No need to access agent logs or emit events to an observability tool
- No need to specify an LLM API key

But it has a number of limitations:

- **Inefficient context usage** — Entity extraction and recall both happen inside the agent's context window, not in a separate process. Full Kaizen offloads all processing to the MCP server, keeping the agent's context free for the actual task.
- **Scalability** — All entities are injected on every prompt. Full Kaizen uses semantic search to retrieve only the relevant subset, which scales to large entity sets.
- **Single-trajectory visibility** — Lite mode only extracts entities from the current session. Full Kaizen can ingest complete trajectories across multiple sessions and glean insights that a single-conversation view would miss.
- **Entity consolidation** — Lite mode simply appends new entities. Full Kaizen performs LLM-based conflict resolution to merge, supersede, or refine entities, and garbage-collects stale ones.

| Capability | Kaizen Lite | Full Kaizen |
|------------|-------------|-------------|
| Entity storage | JSON file | Milvus vector store |
| Retrieval | All entities injected via hooks | Semantic search via MCP |
| Conflict resolution | Append-only | LLM-based merging + garbage collection |
| Trajectory analysis | Current session only (`/kaizen:learn`) | Multi-session, automatic via MCP |
| Context efficiency | Consumes main agent context | Processes separately via MCP |
| Observability | Not required | Ingests from agent logs / trace events |
| Infrastructure | None | MCP server + vector DB + API key |
| Setup time | < 1 minute | ~10 minutes |
