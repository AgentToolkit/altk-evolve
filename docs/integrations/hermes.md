# Hermes Memory Provider

Hermes is the one platform where Evolve Lite is not a prompt-and-skill bundle. Hermes has a first-class `MemoryProvider` interface, so Evolve installs as a Python provider that Hermes imports and calls directly: it learns guidelines from finished sessions and injects the relevant ones before each turn, with no `/learn` command to remember and no hook to wire up.

Like the other lite integrations it needs no vector store, no MCP server, and no second API key — guideline generation runs inside Hermes, through `agent.plugin_llm`, on the model and credentials Hermes is already configured with. The one requirement it inherits from that lane is structured output; see [Prerequisites](#prerequisites).

## Prerequisites

- [Hermes](https://github.com/NousResearch/hermes-agent) installed and configured
- A model on Hermes's auxiliary lane that supports **structured output** (JSON schema). `PluginLlm` routes through `agent.auxiliary_client`, which defaults to your main model — so this is satisfied out of the box on the usual frontier providers. It is worth checking if you have pointed the auxiliary lane at a small or local model.

!!! warning "A model that cannot do structured output fails quietly"
    Guideline generation is a single `complete_structured` call, and capture is written never to break a session. So if the auxiliary model rejects the JSON schema, the result is **zero guidelines** with no user-visible error — the only trace is one line in `agent.log`:

    ```text
    evolve: guideline generation LLM call failed
    ```

    If sessions are ending and `entities/guideline/` stays empty, grep for that line before looking anywhere else.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform hermes
hermes config set memory.provider evolve
```

The install copies the provider bundle to `$HERMES_HOME/plugins/evolve/` (default `~/.hermes/plugins/evolve/`). This is **global**: unlike the Bob and Claude installs there is nothing per-repo, and `--dir` is ignored. Nothing is pip-installed — the bundle is stdlib-only and self-contained.

Check the install the same way — the command above pipes the script straight to `bash`, so there is no local copy to invoke:

```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- status
```

From a clone of the repo, run `platform-integrations/install.sh status` instead.

Instead of `hermes config set`, you can run `hermes memory setup` and pick `evolve` — the provider publishes a config schema, so the wizard walks you through the settings in the [Configuration](#configuration) table.

!!! note "Bundled providers win"
    Hermes discovers memory providers from four places and resolves collisions **bundled first**, then user, then project, then entry point. If your hermes-agent checkout ships its own `plugins/memory/evolve/`, that copy shadows the one in `$HERMES_HOME/plugins/` and your changes here will appear to do nothing. Remove the bundled copy to use this one.

## How It Works

Both halves of the loop are automatic — they run from `MemoryProvider` callbacks, not from anything you type.

**Recall.** After each turn, the provider retrieves guidelines on a background thread; on the next turn they are injected as a numbered list under `Guidelines learned from previous sessions (apply when relevant):`. Retrieval is case-insensitive term overlap between your message and each guideline's trigger + content — lexical, not semantic. Recalled text is passed through Hermes's own context sanitizer before injection, so a guideline that contains something fence-shaped cannot forge a context boundary.

**Capture.** At session end, if the session had at least `min_turns` user turns, the conversation is flattened to an OpenAI-shaped trajectory (system prompts dropped, tool calls inlined, previously-injected guidelines stripped so Evolve cannot re-learn its own output), appended to `trajectories/<session_id>.jsonl`, and passed through a single structured LLM call that applies evolve-lite's `learn` criteria. Each guideline that comes back is saved as a Markdown entity. Capture never breaks a session: every failure mode — no LLM available, a malformed response, an unwritable store — ends in zero guidelines, not an error.

Two details worth knowing:

- **Only primary sessions write.** Subagents and cron jobs get recall but never capture, so background work cannot pollute the store.
- **Session resets capture too.** `/new` in the gateway ends a session without an explicit session-end, so the buffered transcript is captured under the session id that just finished.

!!! warning "Check what ends a session on your install"
    Capture only fires at a session boundary, and `hermes setup` writes `session_reset.mode: none` by default — sessions never auto-reset on idle or on a daily schedule. That leaves two boundaries: an explicit `/new` or `/reset`, and the Hermes process exiting. A long-running gateway that is never restarted and never sees a `/new` will therefore learn nothing, no matter how many turns go by.

    Either pick an auto-reset policy:

    ```bash
    hermes config set session_reset.mode idle
    hermes config set session_reset.idle_minutes 60
    ```

    or leave resets off and have Evolve capture on a turn count instead:

    ```bash
    export EVOLVE_CAPTURE_EVERY_N_TURNS=10
    ```

Every recall that returns something appends a row to `$HERMES_HOME/evolve/audit.log` in the same format the rest of Evolve uses, so the [`provenance`](../guides/guidelines.md) tooling can read Hermes sessions without anything Hermes-specific.

## Tools

Automatic recall covers the common case; these two tools are for when the model wants to act deliberately. Both are on by default and can be turned off with `expose_tools`.

| Tool | Description |
|------|-------------|
| `evolve_get_guidelines(task)` | Look up guidelines for a task other than the current one |
| `evolve_save_guideline(content, trigger, rationale)` | Record a lesson immediately, without waiting for session end |

## Configuration

Set these as environment variables, or as keys in `$HERMES_HOME/evolve/config.json`. Environment variables win.

| Environment variable | `config.json` key | Default | Meaning |
|---|---|---|---|
| `EVOLVE_MODE` | `mode` | `lite` | `lite`, or `server` to disable (see below) |
| `EVOLVE_DIR` | `dir` | `$HERMES_HOME/evolve` | Storage root override |
| `EVOLVE_PREFETCH_LIMIT` | `prefetch_limit` | `5` | Max guidelines injected per turn |
| `EVOLVE_CAPTURE_EVERY_N_TURNS` | `capture_every_n_turns` | `0` (off) | Also capture every N turns, for long sessions that never end cleanly |
| `EVOLVE_MIN_TURNS` | `min_turns` | `2` | Minimum user turns before session-end capture fires |
| `EVOLVE_EXPOSE_TOOLS` | `expose_tools` | `true` | Expose the two `evolve_*` tools to the model |

`EVOLVE_MODE=server` is reserved for a future MCP-backed mode and is not implemented. Setting it disables the provider — deliberately, so a half-configured server mode never silently degrades to doing nothing.

## Storage

```text
$HERMES_HOME/evolve/
  config.json              # optional
  audit.log                # recall provenance, one JSON object per line
  entities/
    guideline/
      use-make-check-for-tests.md
  trajectories/
    <session_id>.jsonl
```

Entity files are the same Markdown-with-YAML-frontmatter format as every other Evolve integration:

```markdown
---
type: guideline
trigger: running tests in a src-layout repo
source: hermes-evolve-lite
---

Use `make check` instead of bare pytest.

## Rationale

Bare pytest fails without PYTHONPATH set.
```

`EVOLVE_DIR` moves the entity and trajectory store; `audit.log` stays under `$HERMES_HOME/evolve/` either way, since it records what one agent install recalled rather than what the store contains.

## Uninstall

```bash
./install.sh uninstall --platform hermes
```

This removes `$HERMES_HOME/plugins/evolve/` and nothing else. The guideline store at `$HERMES_HOME/evolve/` is your data and is left in place — delete it yourself to discard what was learned.

## Limitations

- **Lexical retrieval.** Term overlap, not semantic search. A guideline phrased differently from your request may not surface.
- **No conflict resolution.** Guidelines are appended; duplicates and near-duplicates accumulate. Full Evolve merges and garbage-collects them.
- **No migration path to server mode** yet, so a future upgrade means starting fresh or writing an import script.
- **No reconciliation with Hermes's other stores.** A lesson can end up captured both as an Evolve guideline and as a Hermes skill or memory entry.

For how this compares with full Evolve, see the [Evolve Lite tradeoffs table](claude/evolve-lite.md#tradeoffs).
