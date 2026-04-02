<div align="center">

# Evolve: On‑the‑job learning for AI agents

![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Status](https://img.shields.io/badge/status-active-brightgreen)
![arXiv](https://img.shields.io/badge/arXiv-2603.10600-b31b1b) 
![License](https://img.shields.io/github/license/AgentToolkit/altk-evolve)
![Stars](https://img.shields.io/github/stars/AgentToolkit/altk-evolve?style=social)
</div>

Evolve is a system designed to help agents improve over time by learning from their trajectories. It uses a combination of an MCP server for tool integration, vector storage for memory, and LLM-based conflict resolution to refine its knowledge base.

## Features

- **MCP Server**: Exposes tools to get guidelines and save trajectories.
- **Conflict Resolution**: Intelligently merges new insights with existing guidelines using LLMs.
- **Trajectory Analysis**: Automatically analyzes agent trajectories to generate guidelines and best practices.
- **Milvus Integration**: Uses Milvus (or Milvus Lite) for efficient vector storage and retrieval.

## Architecture

<img src="docs/assets/architecture.png" alt="Architecture" width="480">

## Quick Start

### Installation

Prerequisites:
- Python 3.12 or higher
- `uv` (recommended) or `pip`

```bash
git clone <repository_url>
cd evolve
uv venv --python=3.12 && source .venv/bin/activate
uv sync
```

### Configuration

For direct OpenAI usage:
```bash
export OPENAI_API_KEY=sk-...
```

For LiteLLM proxy usage and model selection (including global fallback via `EVOLVE_MODEL_NAME`), see [CONFIGURATION.md](CONFIGURATION.md).

### Running the MCP Server & UI

Evolve provides both a standard MCP server and a full Web UI (Dashboard & Entity Explorer).

> [!IMPORTANT]
> **Building from Source:** If you cloned this repository (rather than installing a pre-built package), you must build the UI before it can be served.
> ```bash
> cd evolve/frontend/ui
> npm ci && npm run build
> cd ../../../
> ```
> See `evolve/frontend/ui/README.md` for more frontend development details.

#### Starting Both Automatically
The easiest way to start both the MCP Server (on standard input/output) and the HTTP UI backend is to run the module directly:
```bash
uv run python -m evolve.frontend.mcp
```
This will start the UI server in the background on port `8000` and the MCP server in the foreground. You can then access the UI locally by opening your browser to:
`http://127.0.0.1:8000/ui/`

#### Starting the UI Standalone
If you only want to access the Web UI and API (without the MCP server stdio blocking the terminal), you can run the FastAPI application directly using `uvicorn`:
```bash
uv run uvicorn evolve.frontend.mcp.mcp_server:app --host 127.0.0.1 --port 8000
```
Then navigate to `http://127.0.0.1:8000/ui/`.

#### Starting only the MCP Server
If you're attaching Evolve to an MCP client that requires a direct command (like Claude Desktop):
```bash
uv run fastmcp run evolve/frontend/mcp/mcp_server.py --transport stdio
```
Or for SSE transport:
```bash
uv run fastmcp run evolve/frontend/mcp/mcp_server.py --transport sse --port 8201
```

Verify it's running:
```bash
npx @modelcontextprotocol/inspector@latest http://127.0.0.1:8201/sse --cli --method tools/list
```

**Available tools:**
- `get_entities(task: str, entity_type: str)`: Get relevant entities for a specific task, filtered by type (e.g., 'guideline', 'policy').
- `get_guidelines(task: str)`: Get relevant guidelines for a specific task (backward compatibility alias).
- `save_trajectory(trajectory_data: str, task_id: str | None)`: Save a conversation trajectory and generate new guidelines.
- `create_entity(content: str, entity_type: str, metadata: str | None, enable_conflict_resolution: bool)`: Create a single entity in the namespace.
- `delete_entity(entity_id: str)`: Delete a specific entity by its ID.

## Tip Provenance

Evolve automatically tracks the origin of every guideline it generates or stores. Every tip entity contains `metadata` identifying its source:
- `creation_mode`: Identifies how the tip was created (`auto-phoenix` via trace observability, `auto-mcp` via trajectory saving tools, or `manual`).
- `source_task_id`: The ID of the original trace or task that inspired the tip, providing full audibility.

See the [Low-Code Tracing Guide](docs/LOW_CODE_TRACING.md#6-understanding-tip-provenance-metadata) for more details.


## Community & Feedback

Evolve is an active project, and real‑world usage helps guide its direction.

If Evolve is useful or aligned with your work, consider giving the repo a ⭐ — it helps others discover it.  
If you’re experimenting with Evolve or exploring on‑the‑job learning for agents, feel free to open an issue or discussion to share use cases, ideas, or feedback.


## Documentation

- [EVOLVE_LITE.md](EVOLVE_LITE.md) - Lightweight mode via Claude Code plugin (no infra required)
- [CONFIGURATION.md](CONFIGURATION.md) - Detailed configuration options
- [POLICIES.md](docs/POLICIES.md) - Policy support and schema
- [CLI.md](CLI.md) - Command-line interface documentation
- [CLAUDE_CODE_DEMO.md](CLAUDE_CODE_DEMO.md) - Claude Code demo walkthrough

## Development

### Running Tests

The test suite is organized into 4 cleanly isolated tiers depending on infrastructure requirements:

1. **Default Local Suite**
   Runs both fast logic tests (`unit`) and filesystem script verifications (`platform_integrations`).
   ```bash
   uv run pytest
   ```

2. **Unit Tests (Only)**
   Fast, fully-mocked tests verifying core logic and offline pipeline schemas.
   ```bash
   uv run pytest -m unit
   ```

3. **Platform Integration Tests**
   Fast filesystem-level integration tests verifying local tool installation and idempotency.
   ```bash
   uv run pytest -m platform_integrations
   ```

4. **End-to-End Infrastructure Tests**
   Heavy tests that autonomously spin up a background Phoenix server and simulate full agent workflows.
   ```bash
   uv run pytest -m e2e --run-e2e
   ```
   *(See [docs/LOW_CODE_TRACING.md](docs/LOW_CODE_TRACING.md#end-to-end-verification) for more details).*

5. **LLM Evaluation Tests**
   Tests needing active LLM inference to test resolution pipelines (requires LLM API keys).
   ```bash
   uv run pytest -m llm
   ```
