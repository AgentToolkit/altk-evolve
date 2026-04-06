---
template: main.html
---
# Evolve
***_Self-improving agents through iterations._***

Evolve is a system designed to help agents improve over time by learning from their trajectories. It uses a combination of an MCP server for tool integration, vector storage for memory, and LLM-based conflict resolution to refine its knowledge base.

## Features

- **MCP Server**: Exposes tools to get guidelines and save trajectories.
- **Conflict Resolution**: Intelligently merges new insights with existing guidelines using LLMs.
- **Trajectory Analysis**: Automatically analyzes agent trajectories to generate guidelines and best practices.
- **Milvus Integration**: Uses Milvus (or Milvus Lite) for efficient vector storage and retrieval.

## Start Here

- [Installation](installation/index.md): Set up Evolve on Bob or Claude Code.
- [Configuration](guides/configuration.md): Configure models, backends, and environment variables.
- [CLI Reference](reference/cli.md): Manage namespaces, entities, and sync jobs from the command line.

## Guides

- [Low-Code Tracing](guides/low-code-tracing.md): Instrument agents with Phoenix and verify end-to-end tracing.
- [Phoenix Sync](guides/phoenix-sync.md): Pull trajectories from Phoenix and generate stored guidelines.
- [Extract Trajectories](guides/extract-trajectories.md): Export Phoenix traces into an OpenAI-style message format.

## Integrations and Tutorials

- [Evolve Lite (Claude Code)](integrations/claude/evolve-lite.md): Lightweight Claude Code integration with local entity storage.
- [Claude Code Demo](tutorials/claude-code-demo.md): Run the filesystem demo with Claude Code and the Evolve MCP server.
- [Hello World with IBM Bob](examples/hello_world/bob.md): A simple Bob walkthrough that shows how memory gets learned.

## Reference

- [Policies](reference/policies.md): Structured policy entities and how to retrieve them with MCP tools.

## Architecture
![Architecture](assets/architecture.png)
