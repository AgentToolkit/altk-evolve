# Evolve Lite for Hermes

A Hermes memory provider that helps Hermes learn from conversations by automatically extracting and applying guidelines.

⭐ Star the repo: https://github.com/AgentToolkit/altk-evolve

## Features

- Automatic recall through the `MemoryProvider` interface — relevant guidelines are injected before each turn, with no command to run
- Automatic capture at session end, turning the session trajectory into reusable guidelines
- Optional mid-session capture on a turn cadence, for long sessions that rarely end cleanly
- `evolve_get_guidelines` tool to look up guidelines for a task on demand
- `evolve_save_guideline` tool to record a lesson without waiting for session end
- Recall provenance written to `audit.log` in the same format every other Evolve integration uses

## Installation

Use the platform installer from the repo root:

```bash
platform-integrations/install.sh install --platform hermes
```

That installs `$HERMES_HOME/plugins/evolve/` (default `~/.hermes/plugins/evolve/`). Unlike the Bob and Claude installs this is **global** — there is nothing per-repo, and `--dir` is ignored. Nothing is pip-installed; the bundle is stdlib-only.

Then enable it:

```bash
hermes config set memory.provider evolve
```

Or run `hermes memory setup` and pick `evolve` — the provider publishes a config schema, so the wizard walks through the settings below.

Note that Hermes resolves memory-provider name collisions **bundled first**. If your hermes-agent checkout ships its own `plugins/memory/evolve/`, that copy shadows this one and changes here will appear to do nothing.

## How It Works

Both halves of the loop run from `MemoryProvider` callbacks, not from anything the user types.

**Recall.** `queue_prefetch()` retrieves guidelines on a background thread after each turn; `prefetch()` returns the cached result on the next turn, formatted as a numbered list under `Guidelines learned from previous sessions (apply when relevant):`. Retrieval is case-insensitive term overlap between the user's message and each guideline's trigger and content — lexical, not semantic. The formatted block is passed through `agent.memory_manager.sanitize_context` before injection, so stored content cannot forge a context boundary.

**Capture.** At session end, if the session had at least `min_turns` user turns and `agent_context == "primary"`, the conversation is converted by `trajectory_adapter.to_openai_trajectory` (system prompts dropped, tool calls inlined, previously-injected guidelines stripped so Evolve cannot re-learn its own output), appended to `trajectories/<session_id>.jsonl`, and passed to `guideline_gen.generate_guidelines()`. That is a single structured LLM call through `agent.plugin_llm.PluginLlm`, applying the same capture criteria as evolve-lite's `learn` skill, and it runs on the model and credentials Hermes is already configured with — no second API key. Each guideline that comes back is saved as a markdown entity.

Capture never breaks a session: every failure mode — no LLM available, a malformed response, an unwritable store — ends in zero guidelines rather than an error.

Two details worth knowing:

- **Only primary sessions write.** `subagent`, `cron`, and `flush` contexts get recall but never capture, so background work cannot pollute the store.
- **Session resets capture too.** `/new` ends a session without an explicit session-end, so the buffered transcript is captured under the session id that just finished.

## Tools

Automatic recall covers the common case; these two are for when the model wants to act deliberately. Both are on by default and can be turned off with `EVOLVE_EXPOSE_TOOLS`.

### `evolve_get_guidelines(task)`

Look up stored guidelines for a task other than the current one.

### `evolve_save_guideline(content, trigger, rationale)`

Record a lesson immediately, without waiting for session end.

## Storage

Entities, trajectories, and recall provenance are stored globally under:

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

Each entity is a markdown file with lightweight YAML frontmatter, the same format as every other Evolve integration. That format is not re-implemented here: the bundle ships the shared `entity_io.py` at `lib/evolve-lite/entity_io.py` and `backend.py` imports it, so there is one source of truth across integrations. It is loaded by explicit path and registered under a namespaced module key rather than by prepending `lib/evolve-lite/` to `sys.path` — the provider lives inside a long-lived host process, so it adds nothing importable that unrelated code could pick up by accident.

`EVOLVE_DIR` moves the entity and trajectory store; `audit.log` stays under `$HERMES_HOME/evolve/` either way, since it records what one agent install recalled rather than what the store contains.

## Environment Variables

Env vars take precedence; unset ones fall back to the matching key in `$HERMES_HOME/evolve/config.json`.

- `EVOLVE_DIR` (`dir`): Override the default `$HERMES_HOME/evolve` storage root for entities and trajectories.
- `EVOLVE_PREFETCH_LIMIT` (`prefetch_limit`): Max guidelines injected per turn. Default `5`.
- `EVOLVE_MIN_TURNS` (`min_turns`): Minimum user turns before session-end capture fires. Default `2`.
- `EVOLVE_CAPTURE_EVERY_N_TURNS` (`capture_every_n_turns`): Also capture every N turns, for long sessions that never end cleanly. Default `0` (off).
- `EVOLVE_EXPOSE_TOOLS` (`expose_tools`): Expose the two `evolve_*` tools to the model. Default `true`.

## Verification

After installation, verify that:

- `$HERMES_HOME/plugins/evolve/` exists
- `hermes config get memory.provider` returns `evolve`
- `$HERMES_HOME/evolve/entities/guideline/` fills up after a couple of sessions end

You can also run:

```bash
platform-integrations/install.sh status
```

## Plugin Structure

```text
evolve/
├── plugin.yaml
├── __init__.py                  # the MemoryProvider subclass Hermes loads
├── backend.py                   # filesystem store: retrieval and writes
├── guideline_gen.py             # the single structured LLM call
├── trajectory_adapter.py        # Hermes messages -> OpenAI-shaped trajectory
├── README.md
└── lib/evolve-lite/             # shared library, copied in at build time
```

For install, configuration, and troubleshooting docs, see
<https://agenttoolkit.github.io/altk-evolve/integrations/hermes/>.
