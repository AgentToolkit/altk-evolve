<div align="center">

# Evolve: On‑the‑job learning for AI agents

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
![Status](https://img.shields.io/badge/status-active-brightgreen)
[![Documentation](https://shields.io/badge/Official%20Webpage-Documentation-blue)](https://agenttoolkit.github.io/altk-evolve)
[![arXiv](https://img.shields.io/badge/arXiv-2603.10600-b31b1b)](https://arxiv.org/pdf/2603.10600)
[![License](https://img.shields.io/github/license/AgentToolkit/altk-evolve)](https://www.apache.org/licenses/LICENSE-2.0)
![Stars](https://img.shields.io/github/stars/AgentToolkit/altk-evolve?style=social)

**Blog posts:** [IBM announcement](https://www.ibm.com/new/announcements/altk-evolve-on-the-job-learning-for-ai-agents) | [Hugging Face blog](https://huggingface.co/blog/ibm-research/altk-evolve)
</div>

Coding agents repeat the same mistakes because they start fresh every session. Evolve gives agents memory — they learn from what worked and what didn't, so each session is better than the last.

Evolve is a system designed to help agents improve over time by learning from their trajectories. The Lite version is designed to effortlessly slot into existing agent assistants like Claude Code and Codex. It uses a combination of an MCP server for tool integration, vector storage for memory, and LLM-based conflict resolution to refine its knowledge base.

On the AppWorld benchmark, Evolve improved agent reliability by +8.9 points overall, with a 74% relative increase on hard multi-step tasks. Evolve is a system designed to help agents improve over time by learning from their trajectories. It uses a combination of an MCP server for tool integration, vector storage for memory, and LLM-based conflict resolution to refine its knowledge base.

> [!IMPORTANT]
> ⭐ **Star the repo**: it helps others discover it.  

## Quick Start (Lite)
[IBM Bob →](https://agenttoolkit.github.io/altk-evolve/examples/hello_world/bob/)

[Claude Code →](https://agenttoolkit.github.io/altk-evolve/examples/hello_world/claude)

[Codex →](https://agenttoolkit.github.io/altk-evolve/examples/hello_world/codex/)

## Quick Start (Evolve MCP Server)
### Installation
Prerequisites:
- Python 3.12 or higher
- `uv` (recommended) or `pip`

From Source
```bash
# Clone the repository and install dependencies
git clone https://github.com/agenttoolkit/altk-evolve.git
cd altk-evolve
uv venv --python=3.12 && source .venv/bin/activate
uv sync
# Build the UI
cd frontend/ui
npm ci && npm run build
cd ../..
```
From PyPI
```bash
pip install altk-evolve
```

**Optional Backend Dependencies:**

The default filesystem backend uses simple text matching and requires no additional dependencies. For semantic vector similarity search, install one of these backends:

For PostgreSQL with pgvector support (recommended for production):
```bash
uv sync --extra pgvector
```

For Milvus support (optimized for large-scale vector search):
```bash
uv sync --extra milvus
```

See the [Backend Configuration Guide](docs/guides/backend-configuration.md) for detailed comparison and setup instructions.

### Configuration

For direct OpenAI usage:
```bash
export OPENAI_API_KEY=sk-...
```

For LiteLLM proxy usage and model selection (including global fallback via `EVOLVE_MODEL_NAME`), see [the configuration guide](docs/guides/configuration.md).

### Running Services
Start the Web UI and MCP server
```bash
uv run evolve-mcp
```
The Web UI can be accessed from: `http://127.0.0.1:8000/ui/`

### Starting the Web UI and MCP Server
If you only want to access the Web UI and API (without the MCP server stdio blocking the terminal), you can run the FastAPI application directly using `uvicorn`:
```bash
uv run uvicorn altk_evolve.frontend.mcp.mcp_server:app --host 127.0.0.1 --port 8000
```
Then navigate to `http://127.0.0.1:8000/ui/`.

### Starting only the MCP Server
If you're attaching Evolve to an MCP client that requires a direct command (like Claude Desktop):
```bash
uv run evolve-mcp
```
Or for SSE transport:
```bash
uv run evolve-mcp --transport sse --port 8201
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

### Filter Migration Note
Entity search filters reserve bare keys for top-level schema columns only: `id`, `type`, `content`, and `created_at`.

If you need to filter on JSON metadata, use the `metadata.<key>` form. For example, use `filters={"type": "trajectory", "metadata.task_id": "123"}` instead of `filters={"type": "trajectory", "task_id": "123"}`.

Existing integrations that stored custom fields in entity metadata should update filter writers to add the `metadata.` prefix for those keys.

## Features
- **Proactive**: Learns how to recognize problems and their solutions, and generates guidelines that get automatically applied to new tasks.
- **Conflict Resolution**: Update existing guidelines when new information contradicts them.
- **On Command**: An array of tools to manage guidelines whether in the agent or through a CLI

## Architecture
Evolve is built on a modular architecture which forms a feedback loop, taking conversation traces (trajectories) from an agent, extracting key insights into a database, feeding it back into the agent.

_Lite Mode omits the Interaction layer. All activity is performed in-agent_
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture-wide-dark.svg">
  <img src="docs/assets/architecture-wide-light.svg" alt="Architecture" width="480">
</picture>

## Tip Provenance
Evolve automatically tracks the origin of every guideline it generates or stores. Every tip entity contains `metadata` identifying its source:
- `creation_mode`: Identifies how the tip was created (`auto-phoenix` via trace observability, `auto-mcp` via trajectory saving tools, or `manual`).
- `source_task_id`: The ID of the original trace or task that inspired the tip, providing full audibility.

See the [Low-Code Tracing Guide](docs/guides/low-code-tracing.md#6-understanding-tip-provenance-metadata) for more details.


## Contributing, Community, and Feedback
Evolve is an active project, and real‑world usage helps guide its direction.

If you’re experimenting with Evolve or exploring on‑the‑job learning for agents, feel free to open an issue or discussion to share use cases, ideas, or feedback.

See the [Contributing Guide](CONTRIBUTING.md) to understand our development process, or how to submit changes, report bugs, or propose features.
