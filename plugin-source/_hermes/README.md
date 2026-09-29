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

**Recall.** `queue_prefetch()` warms a cache on a background thread after each turn; `prefetch()` serves it on the next turn if it was scored for that same session and question, and otherwise does the lookup itself. Hermes shows `🧠 Evolve — recalled 3 memories` above the reply on any turn that injected something, from `recall_status()` — so recall is visible without depending on the model to mention it. That fallback is what makes the first turn of a session work — retrieval is a filesystem scan of sub-kilobyte files, not a network call. Results are formatted as a numbered list under `Guidelines learned from previous sessions (apply when relevant):`. Retrieval is case-insensitive term overlap between the user's message and each guideline's trigger and content — lexical, not semantic. Each field is collapsed to one line with `<`/`>` escaped, and the finished block is passed through `agent.memory_manager.sanitize_context`, so stored content cannot forge a context boundary or a list item of its own.

**Capture.** At session end, if the session had at least `min_turns` user turns and `agent_context == "primary"`, the conversation is converted by `trajectory_adapter.to_openai_trajectory` (system prompts dropped, tool calls inlined, previously-injected guidelines stripped so Evolve cannot re-learn its own output), appended to `trajectories/<session_id>.jsonl`, and passed to `guideline_gen.generate_guidelines()`. That is a single structured LLM call through `agent.plugin_llm.PluginLlm`, applying the same capture criteria as evolve-lite's `learn` skill, and it runs on the model and credentials Hermes is already configured with — no second API key. Each guideline that comes back is saved as a markdown entity.

Capture never breaks a session: every failure mode — no LLM available, a malformed response, an unwritable store — ends in zero guidelines rather than an error.

Two details worth knowing:

- **Only primary sessions write.** `subagent`, `cron`, and `flush` contexts get recall but never capture, so background work cannot pollute the store.
- **Session resets capture too.** `/new` ends a session without an explicit session-end, so the buffered transcript is captured under the session id that just finished.
- **Guidelines are screened before they are stored.** Both write paths run `tools.threat_patterns.scan_for_threats` over the content, trigger, and rationale — `strict` scope for `evolve_save_guideline` (the model gets the refusal back and can rewrite), `context` scope for generated guidelines (the set Hermes applies to memory entries, which is what a recalled guideline becomes). Screening on the way in rather than scrubbing on the way out: a guideline that trips these patterns would be stripped at recall anyway, so storing it only banks an entry that can never be used.

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

`EVOLVE_DIR` moves the entity and trajectory store; `audit.log` stays under `$HERMES_HOME/evolve/` either way, since it records what one agent install recalled rather than what the store contains. One consequence worth knowing: the `provenance` skill resolves both the audit log and the trajectory under a single root, so it only works when that root holds both. Run it with `EVOLVE_DIR=$HERMES_HOME/evolve` — the pairing the provider itself writes when `EVOLVE_DIR` is unset. Leaving the variable unset while running the skill is not the same thing: the skill defaults to `./.evolve` in the current directory, not to `$HERMES_HOME`, so it finds nothing unless you happen to be standing in the right place.

Directories the provider creates are `0o700` and the files it writes are `0o600` — guidelines and trajectories are session-derived content, and a default umask would leave them world-readable. A directory that already exists is left as it is, so a store you have deliberately shared is not narrowed out from under you.

### Who the store belongs to

By default there is one store per Hermes install, shared by every session. That is the intent: guidelines are meant to be generalized procedures with no user-specific content, and sharing them is how a second session benefits from the first.

It stops being the intent when strangers share one install — a Discord or Telegram gateway, say — because anything one user's session produces is then injected into everyone else's turns. Set `EVOLVE_SCOPE=user` or `EVOLVE_SCOPE=chat` for those deployments and the store partitions into `users/<slug>-<digest>/` or `chats/<slug>-<digest>/` subdirectories. The slug is there to make the directory recognisable; the digest is a truncated SHA-256 of the id and is what actually identifies the bucket, so two ids that slug to the same string do not share a store. Existing stores are untouched; the default is unchanged.

Scoping applies to new writes only. If Hermes supplies no id for the scope you asked for, that session recalls from the shared store but **captures nothing** — neither a shared "unknown" bucket nor a quiet write to the shared root is acceptable, because both pool exactly the users the scope exists to separate while the directory layout still looks partitioned. The refusal is logged at warning level. Captured trajectories record `user_id`, `chat_id`, and `agent_context` whatever the scope is set to, so a store written before you turned scoping on is still attributable.

## Environment Variables

Env vars take precedence; unset ones fall back to the matching key in `$HERMES_HOME/evolve/config.json`.

- `EVOLVE_DIR` (`dir`): Override the default `$HERMES_HOME/evolve` storage root for entities and trajectories.
- `EVOLVE_SCOPE` (`scope`): Partition the store — `global` (default, one shared store), `user`, or `chat`. See "Who the store belongs to" above.
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
