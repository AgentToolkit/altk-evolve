---
template: main.html
hide:
  - navigation
  - toc
---
# Evolve
***_Self-improving agents through iterations._***

Evolve helps AI agents learn from experience. It turns past trajectories into reusable guidelines that help agents complete future tasks, without changing model weights.

The Lite version is designed to effortlessly slot into existing agent assistants like Claude Code and Codex. The full service uses a combination of an MCP server for tool integration, vector storage for memory, and LLM-based conflict resolution to refine its knowledge base. It also supports guideline consolidation, clustering related guidelines and merging equivalent advice while preserving support counts and source provenance.

On AppWorld's held-out test split, a GPT-4.1 ReAct agent with Evolve increased scenario completion from **50.0% to 64.3% (+14.3 percentage points)**.

[The Announcement →](https://www.ibm.com/new/announcements/altk-evolve-on-the-job-learning-for-ai-agents){ .md-button } [Check out the Blog →](https://huggingface.co/blog/ibm-research/altk-evolve){ .md-button } [Read the Paper →](https://arxiv.org/abs/2603.10600){ .md-button } [See the Results →](results/index.md){ .md-button }

<github-repo-card repo="agenttoolkit/altk-evolve"></github-repo-card>

## Research highlights

| Finding | Reported result |
|---|---|
| [More repeatable success](results/index.md#repeat-run-consistency) | AppWorld tasks passing all five attempts increased from **53% to 66%**, using sibling-task guidelines with GPT-4.1. |
| [Efficient learning](results/index.md#model-coverage-and-efficiency) | With gpt-oss-120b, AppWorld task completion increased from **39.9% to 56.0%** using curated retrieval. |
| [Lifts frontier and non-frontier models](results/index.md#customer-service-tasks) | On τ²-bench, Evolve lifts both frontier and non-frontier models (held-out Pass^4: GPT-5.5 **55% → 70%**; GPT-4.1 **57.5% → 67.5%**). |

These are separate benchmark experiments with different metrics, models, and memory configurations. Follow each link for its evaluation protocol and sources.

## Latest from Evolve

Research, releases, and practical findings from the Evolve team.

--8<-- "includes/latest-updates.md"

=== "Lite"
    When setting up API keys and extra services are too much

    [General Installation](installation/index.md){ .md-button }

    [Claude Code](examples/hello_world/claude.md){ .md-button } [IBM Bob](examples/hello_world/bob.md){ .md-button } [Codex](examples/hello_world/codex.md){ .md-button } [Hermes](integrations/hermes.md){ .md-button }

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

## Demos

=== "Claude Code"

    <div class="video-wrapper">
        <iframe width="1280" height="720" src="https://www.youtube.com/embed/XIlYA79pYp4" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
    </div>

=== "IBM Bob"

    !!! note "The video shows an earlier release"
        This April 2026 video uses an Evolve Lite mode and explicit learn/recall skills. Current Bob releases use always-on rules. Follow the [current tutorial](examples/hello_world/bob.md) for installation and usage.

    <div class="video-wrapper">
        <iframe width="1280" height="720" src="https://www.youtube.com/embed/YlTJoSJ4eDg" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
    </div>

=== "Codex"

    <div class="video-wrapper">
        <iframe width="1280" height="720" src="https://www.youtube.com/embed/IBc59bLjdi8" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
    </div>

## How It Works

Evolve analyzes agent trajectories to extract guidelines and best practices, then recalls them in future sessions. It supports both a lightweight file-based mode (Evolve Lite) and a full mode backed by an MCP server with vector storage, LLM-based conflict resolution, and guideline consolidation.
<figure>
    <picture class="arch light">
        <source media="(min-width: 1000px)" srcset="assets/architecture-wide-light.svg">
        <img src="assets/architecture-light.svg" alt="Architecture">
    </picture>
    <picture class="arch dark">
        <source media="(min-width: 1000px)" srcset="assets/architecture-wide-dark.svg">
        <img src="assets/architecture-dark.svg" alt="Architecture">
    </picture>
    <!-- <figcaption>Does your agent make the same mistake twice?</figcaption> -->
</figure>
