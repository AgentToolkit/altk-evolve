# Starter Example: Evolving Agent Loop

This tutorial takes you through the end-to-end evolving agent loop: an existing agent runs and is traced, guidelines are generated from that trace, and those guidelines are then fed back into a *new* run of the same agent, so it actually learns from what it did before.

This tutorial covers **full Evolve (MCP server / CLI)**. It doesn't apply to [Evolve Lite](../integrations/claude/evolve-lite.md), where the equivalent loop (`/evolve-lite:learn`, then automatic injection on the next prompt) is handled entirely by the host agent — see the [Claude Code starter tutorial](../examples/hello_world/claude.md) for that version.

!!! note "Set expectations"
    The example agent here (`examples/low_code/smolagents_demo.py`) does simple arithmetic with two tools, so there's little room for it to behave meaningfully differently after "learning" — don't expect a dramatic before/after. The goal of this tutorial is to demonstrate the full wiring (trace → sync → generate → retrieve → inject) end to end on a small, fast example. Point the same steps at a real agent with real failure modes to see guidelines actually change behavior.

## What you'll build

You'll run a small pipeline end to end, in five stages:

1. **Run** — a smolagents agent (`smolagents_demo.py`) executes a task, with tracing enabled.
2. **Trace** — that run is captured as a trajectory in Phoenix.
3. **Generate** — `evolve sync phoenix` pulls the trajectory out of Phoenix and generates guideline entities from it.
4. **Verify** — you'll confirm those guidelines were actually stored.
5. **Retrieve & inject** — a second script (`guidelines_retrieval_demo.py`) fetches the relevant guidelines and re-runs the same agent with them injected as instructions, closing the loop.

By the end, you'll have watched a guideline travel all the way from "something the agent did" to "something the agent is told to do differently next time."

## Requirements

- [`uv` installed](https://docs.astral.sh/uv/getting-started/installation/)
- An LLM API key for your provider (e.g. `OPENAI_API_KEY`)

Run every command below from the repo root (don't `cd` into `examples/low_code`, even to run the example scripts — Python adds a script's own directory to its import path automatically). This matters because the filesystem backend used throughout (`EVOLVE_BACKEND=filesystem`) stores data relative to the current directory; staying anchored at the repo root keeps every step reading and writing the same `evolve_data/`.

## Step 0: Install dependencies

```bash
uv sync --extra examples
```

Arize Phoenix and `sentence-transformers` (used for guideline retrieval) are core dependencies and come with any `altk-evolve` install. This step additionally pulls in `smolagents`, the OpenInference tracing instrumentors, and the example scripts' other dependencies.

## Step 1: Start Phoenix

```bash
uv run phoenix serve
# Server runs at http://localhost:6006
```

## Step 2: Run the agent with tracing enabled

```bash
EVOLVE_AUTO_ENABLED=true \
EVOLVE_TRACING_PROJECT=guidelines-tutorial \
uv run python examples/low_code/smolagents_demo.py
```

`smolagents_demo.py` runs a `CodeAgent` with two tools (`add`, `multiply`) against the task "What is (5 * 5) + 10?". With `EVOLVE_AUTO_ENABLED=true`, [Low-Code Tracing](../guides/low-code-tracing.md) patches the agent's LLM calls so the full trajectory reaches Phoenix.

Run it two or three times so there's more than one trajectory to generate guidelines from.

## Step 3: Sync the trace into Evolve and generate guidelines

```bash
EVOLVE_BACKEND=filesystem \
uv run evolve sync phoenix \
    --project guidelines-tutorial \
    --namespace guidelines-tutorial \
    --guidelines-mode regular
```

See [Phoenix Sync](../guides/phoenix-sync.md) for the full set of sync options, and [Enabling Guidelines](../guides/guidelines.md) if you want to try `--guidelines-mode consistency` or `both` instead.

## Step 4: Verify guidelines exist

```bash
EVOLVE_BACKEND=filesystem \
uv run evolve entities list guidelines-tutorial --type guideline
```

You should see one or more `guideline` entities, each carrying `metadata.creation_mode: "auto-phoenix"` and `metadata.generation_method: "regular"`.

## Step 5: Retrieve guidelines and re-run the agent with them injected

```bash
EVOLVE_BACKEND=filesystem \
uv run python examples/low_code/guidelines_retrieval_demo.py --namespace guidelines-tutorial --task "What is (5 * 5) + 10?"
```

This script:

1. Calls `EvolveClient().select_guidelines(namespace_id, task_query)` — the same dosage-aware core + top-k retrieval used by the `get_relevant_guidelines` MCP tool — to fetch guidelines relevant to the task.
2. Formats the retrieved guideline content into a plain-text instructions block.
3. Passes that block as `instructions=` when constructing a fresh `CodeAgent`, then re-runs the same task.

The printed output shows exactly which guidelines were retrieved and injected before the agent runs, so you can confirm the loop closed even though the toy task's final answer won't visibly change.

## What's next

- Point `EVOLVE_TRACING_PROJECT` / `--project` at a real agent with real tool failures and retry loops — that's where injected guidelines start visibly changing behavior.
- Swap `EVOLVE_BACKEND=filesystem` for Milvus or Postgres for persistent, semantic retrieval — see [Configuration](../guides/configuration.md).
- Try `--guidelines-mode consistency` in Step 3 to generate guidelines focused on the trajectory's least-stable steps instead — see [Enabling Guidelines](../guides/guidelines.md).
