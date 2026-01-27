# What is Kaizen?
Kaizen is a Python library and service which enables AI agents to improve through self-reflection.

## Key Concepts
- **Trajectory**: A recorded agent conversation
- **Entity**: Anything which is appropriately stored in a vector database, such as a guideline, policy, or some other knowledge.
- **Namespace**: Isolated storage for entities
- **Conflict Resolution**: LLM-based merging of duplicate/conflicting entities
- **Guidelines**: Instructions intended to assist an agent in completing some task

## Architecture Flow
1. Agent completes some task, and the resulting trajectory is automatically saved into a logging framework such as Langfuse or Arize Phoenix.
2. The agent can call the sync MCP function, or the user can manually sync, which causes kaizen to process the trajectory and save any generated guidelines.
3. Generated guidelines are stored as entities with conflict resolution applied.
4. Future agents can query the Kaizen MCP server to fetch guidelines for similar tasks

## Project Directory Tree (Some files omitted for brevity)
```text
.
├── demo (Files used by the Claude Code demo)
│   ├── filesystem
│   └── workdir
├── docs (Data used by README files)
├── explorations (Tangential projects for feeling out future work. Should be avoided unless otherwise prompted.)
│   └── claudecode
├── kaizen (Primary Source Root)
│   ├── backend (Entity Database Backend implementations, primarily vector databases)
│   ├── config (All configurations which are derived from environment variables or instantiated as an object)
│   ├── db (A sqlite database for when vector databases are a poor fit for the data)
│   ├── frontend (Interfaces to interact with the backend)
│   │   ├── cli (A CLI wrapper over the native Python client)
│   │   ├── client (A native Python client which thinly wraps the configured backend)
│   │   └── mcp (An MCP server implementing some high-level methods useful for AI agents)
│   ├── llm (All code that prompts an LLM)
│   ├── schema (All well-defined datatypes used throughout the project)
│   ├── sync (Upstream data sources to be processed and stored in the backend)
│   └── utils (Small reusable code snippets)
├── tests (All tests for kaizen)
├── .env.example (Environment variable template file)
└── .env (Environment variables used to configure kaizen)
```

## First Time Setup
```bash
uv sync && source .venv/bin/activate
cp .env.example .env  # Configure any environment variables, defined in `./kaizen/config`
pre-commit install
```

## Testing Instructions
- Run pytest verbosely with the `-v` flag by default so that you have more context when tests fail.
- Use `uv run pytest tests/.../<test_name.py>` to run tests individually.
- We use the pytest markers `e2e` for end-to-end tests, and `unit` for unit tests, and `phoenix` to test integration with Phoenix.
- When running `uv run pytest` it will skip the tests marked with `phoenix`.
- To run specific markers: `uv run pytest -m e2e` or `uv run pytest -m unit`
- To override and run all: `uv run pytest -m "e2e or unit or phoenix"`

## Available Interfaces
- MCP Server: `get_guidelines()`, `save_trajectory()`
- CLI: Run `kaizen --help` if details are needed about its subcommands.
  Available subcommands include `namespaces`, `entities`, and `sync`
- Python Client: `KaizenClient()` for programmatic access

## Coding Standards
- Use Ruff for linting and formatting (configured in pyproject.toml)
- Run pre-commit hooks before committing
- All new features need tests (unit + e2e where applicable)