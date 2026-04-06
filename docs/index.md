---
template: main.html
hide:
  - navigation
  - toc
---
# Evolve
***_Self-improving agents through iterations._***

Coding agents repeat the same mistakes because they start fresh every session. Evolve gives agents memory — they learn from what worked and what didn't, so each session is better than the last.

On the AppWorld benchmark, Evolve improved agent reliability by **+8.9 points** overall, with a **74% relative increase** on hard multi-step tasks. See the [full results](results/index.md) and the [paper (arXiv:2603.10600)](https://arxiv.org/abs/2603.10600).
Evolve is a system designed to help agents improve over time by learning from their trajectories. It uses a combination of an MCP server for tool integration, vector storage for memory, and LLM-based conflict resolution to refine its knowledge base.

=== "Lite"
    When setting up API keys and extra services are too much

    [General Installation](installation/index.md){ .md-button }

    [Claude Code](examples/hello_world/claude.md){ .md-button } [IBM Bob](examples/hello_world/bob.md){ .md-button } [Codex](#){ .md-button }

=== "Full"
    Total Control

    !!! bug "Under Development"

    <div class="grid cards" markdown>

    - :octicons-mcp-24: **MCP Server**

        ---

        Exposes tools to get guidelines and save trajectories.

    - :octicons-git-merge-24: **Conflict Resolution**
        
        ---

        Intelligently merges new insights with existing guidelines using LLMs.
    - :octicons-flowchart-24: **Trajectory Analysis** 

        ---

        Automatically analyzes agent trajectories to generate guidelines and best practices.
    - :octicons-database-24: **Milvus Integration**

        ---

        Uses Milvus (or Milvus Lite) for efficient vector storage and retrieval.

    </div>

    ## Guides

    - [Configuration](guides/configuration.md): Configure models, backends, and environment variables.
    - [Low-Code Tracing](guides/low-code-tracing.md): Instrument agents with Phoenix and verify end-to-end tracing.
    - [Phoenix Sync](guides/phoenix-sync.md): Pull trajectories from Phoenix and generate stored guidelines.
    - [Extract Trajectories](guides/extract-trajectories.md): Export Phoenix traces into an OpenAI-style message format.

    ## Reference

    - [CLI Reference](reference/cli.md): Manage namespaces, entities, and sync jobs from the command line.
    - [Policies](reference/policies.md): Structured policy entities and how to retrieve them with MCP tools.

## How It Works

Evolve analyzes agent trajectories to extract guidelines and best practices, then recalls them in future sessions. It supports both a lightweight file-based mode (Evolve Lite) and a full mode backed by an MCP server with vector storage and LLM-based conflict resolution.
<figure>
    <img class="arch-narrow" src="assets/architecture.svg"      alt="Architecture">
    <img class="arch-wide"   src="assets/architecture-wide.svg" alt="Architecture">
    <!-- <figcaption>Does your agent make the same mistake twice?</figcaption> -->
</figure>
