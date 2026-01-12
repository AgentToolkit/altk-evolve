# Kaizen

Self-improving agents through iterations.

Kaizen is a system designed to help agents improve over time by learning from their trajectories. It uses a combination of an MCP server for tool integration, vector storage for memory, and LLM-based conflict resolution to refine its knowledge base.

## Features

- **MCP Server**: Exposes tools to get guidelines and save trajectories.
- **Conflict Resolution**: Intelligently merges new insights with existing guidelines using LLMs.
- **Trajectory Analysis**: Automatically analyzes agent trajectories to generate tips and best practices.
- **Milvus Integration**: Uses Milvus (or Milvus Lite) for efficient vector storage and retrieval.

## Installation

Prerequisites:
- Python 3.12 or higher
- `uv` (recommended) or `pip`

```bash
git clone <repository_url>
cd kaizen
uv sync
```

## Configuration

Kaizen uses environment variables for configuration. You can set these in a `.env` file or export them directly.

**LLM Configuration (required for OpenAI models):**
```bash
export OPENAI_API_KEY=sk-...
```

**Kaizen Configuration:**

All configuration variables are prefixed with `KAIZEN_`.

| Variable | Description | Default |
|----------|-------------|---------|
| `KAIZEN_TIPS_MODEL` | Model for generating tips | `openai/gpt-4o` |
| `KAIZEN_CONFLICT_RESOLUTION_MODEL` | Model for resolving conflicts | `openai/gpt-4o` |
| `KAIZEN_URI` | Milvus URI (file path for Lite) | `katas.milvus.db` |
| `KAIZEN_USER` | Milvus user (optional) | `""` |
| `KAIZEN_PASSWORD` | Milvus password (optional) | `""` |
| `KAIZEN_DB_NAME` | Milvus database name (optional) | `""` |
| `KAIZEN_TOKEN` | Milvus token (optional) | `""` |
| `KAIZEN_TIMEOUT` | Milvus timeout (optional) | `None` |
| `KAIZEN_EMBEDDING_MODEL` | Embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| `KAIZEN_NAMESPACE_ID` | Namespace ID for isolation | `kaizen` |

## Usage

### Running the MCP Server

You can run the MCP server using `fastmcp`:

```bash
# Assuming you are in the root directory
uv run fastmcp run kaizen/frontend/mcp/mcp_server.py
```

This will start the MCP server, which exposes the following tools:
- `get_guidelines(task: str)`: Get relevant guidelines for a specific task.
- `save_trajectory(trajectory_data: str, task_id: str | None)`: Save a conversation trajectory and generate new tips.

## Development

To run tests:

```bash
uv run pytest
```
