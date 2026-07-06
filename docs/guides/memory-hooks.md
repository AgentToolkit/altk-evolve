# Memory Hooks

`altk_evolve` exposes a pluggable hook seam around every memory operation and every LLM egress point. Plugins can normalize metadata, redact PII, audit access, filter recall results, or block operations entirely — without any change to Evolve's core code paths.

## Motivation

Evolve's memory store sits between agents and durable state. Several concerns want to intercept that boundary:

- **Compliance** — redact PII before it is persisted or sent to an LLM.
- **Normalization** — stamp canonical metadata (e.g. `trace_id`, `created_at`) so downstream consumers can rely on it.
- **Access auditing** — record when memories were last recalled.
- **Recall filtering** — drop or transform results before they reach the caller.

Rather than baking each concern in, Evolve defines its own hook types and delegates plugin execution to the [CPEX plugin framework](https://pypi.org/project/cpex/) — the same intended-usage pattern the framework was designed for (a thin wrapper layer per host application).

## Design at a glance

- **Optional dependency.** `cpex` pulls heavy transitive dependencies (fastapi, mcp, prometheus), so it lives behind an extra: `pip install 'altk-evolve[hooks]'`. Without it — or with `hooks.enabled=False`, the default — every hook site is a fast no-op (a boolean check), and behavior is byte-for-byte identical to previous releases.
- **Backend-layer choke points.** Write/read hooks fire inside `BaseEntityBackend` template methods, so no frontend (client, MCP server, CLI, Phoenix sync) can bypass them. Backends override protected `_*_impl` methods only; the public methods own hook dispatch.
- **Frozen payloads.** Payloads are immutable pydantic models; plugins return a `model_copy(update={...})` via `PluginResult.modified_payload`. Transform-mode plugins chain; the final payload flows back to the call site. This is enforced, not just convention: mutable payload contents are deep-copied at dispatch, and a plugin that mutates a payload in place without returning `modified_payload` has its mutation discarded.
- **Halting raises, never drops.** A plugin that halts the pipeline (`continue_processing=False`) raises `altk_evolve.hooks.MemoryPolicyViolation` — a blocked write is an error the caller sees, not a silent no-op.
- **Sync bridge.** CPEX's `invoke_hook` is async-only; Evolve's call sites are sync. The seam uses `asyncio.run` when no event loop is running and a dedicated thread when one is. Fire-and-forget plugin tasks are awaited before the bridge returns so their side effects are never lost with the closing loop.
- **Singleton caveat.** CPEX's `PluginManager` is a process-wide (Borg) singleton — the hook seam is process-global, not per-client. Two sharp edges follow: (a) constructing a second `EvolveClient` with `hooks.enabled=True` calls `PluginManager.reset()` and silently **replaces** the first client's plugins — for a compliance plugin (e.g. PII redaction) this means redaction can be silently disabled by unrelated code constructing its own client; (b) a client constructed with `enabled=False` does not reset the manager, but it still inherits whatever process-global hooks another client enabled — its operations flow through those plugins too. Per-instance isolation (CPEX's `TenantPluginManager`) is deferred until a real use case needs it.

## Hook taxonomy

| Hook type | Fires | Mode semantics | Payload |
|---|---|---|---|
| `memory_pre_write` | In `update_entities`, after namespace validation, **before** conflict resolution (so transforms run before content reaches an LLM) | transform / halt | `namespace_id`, `entities` (content + metadata dicts) |
| `memory_pre_metadata_patch` | Before `update_entity_metadata` merges a patch | transform / halt | `namespace_id`, `entity_id`, `metadata_patch` |
| `memory_pre_delete` | Before the public `delete_entity_by_id` | halt | `namespace_id`, `entity_id` |
| `memory_pre_namespace_delete` | Before `delete_namespace` | halt | `namespace_id` |
| `memory_post_read` | On public `search_entities` results only — internal reads (conflict-resolution pre-reads, the metadata-patch read-before-merge) never fire it | transform (filter/redact) / observe | `namespace_id`, `entities`, `query`, `filters` |
| `llm_pre_call` | Immediately before every litellm `completion` (fact extraction, guidelines, segmentation, clustering, conflict resolution) | transform (redact) / halt | `messages`, `purpose` (call-site tag), `model` |

Recursion safety: a `memory_post_read` plugin that patches metadata goes through `update_entity_metadata`, whose read-before-merge uses the internal `_search_entities_impl` seam — plus a context-local guard suppresses nested `memory_post_read` dispatch.

**Known limitation — LLM-initiated deletes.** Conflict resolution inside `update_entities` can produce DELETE verdicts against *stored* entities; those internal deletes do **not** fire `memory_pre_delete`. `memory_pre_write` fires earlier on the same call, but its payload is the incoming entity batch — not the stored entities the DELETE verdicts target — so `memory_pre_delete` subscribers (e.g. a legal-hold plugin) cannot veto LLM-initiated deletions. Covering these is slated for the future lifecycle/policy hook family (see [Deferred](#deferred)).

## Enabling hooks

Hooks are configured on `EvolveConfig.hooks` and initialized by `EvolveClient`:

```python
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.hooks import HookPluginSpec, HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient

config = EvolveConfig(
    hooks=HooksConfig(
        enabled=True,
        # Either point at a CPEX plugins.yaml...
        plugins_yaml="examples/hooks_plugins.yaml",
        # ...or declare plugins in code (both may be combined):
        plugins=[
            HookPluginSpec(
                name="metadata_normalizer",
                kind="altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin",
                hooks=["memory_pre_write"],
                mode="transform",
            ),
        ],
    )
)
client = EvolveClient(config)
```

See [`examples/hooks_plugins.yaml`](https://github.com/AgentToolkit/altk-evolve/blob/main/examples/hooks_plugins.yaml) for the YAML form and [`examples/hooks_demo.py`](https://github.com/AgentToolkit/altk-evolve/blob/main/examples/hooks_demo.py) for a runnable end-to-end demo.

## Shipped plugins

| Plugin | Hooks | Mode | What it does |
|---|---|---|---|
| `MetadataNormalizerPlugin` | `memory_pre_write` | transform | Copies `task_id` → `trace_id` when only the former is present (MCP-saved trajectories vs Phoenix-synced ones) and stamps `created_at` |
| `AccessStampPlugin` | `memory_post_read` | fire_and_forget | Stamps `last_accessed` (ISO-8601 UTC) on read entities via the metadata-patch path |
| `PIIFilterMemoryPlugin` | `memory_pre_write`, `llm_pre_call` | transform | Regex PII redaction (aliases the native `cpex-pii-filter` plugin onto Evolve's hook types); requires `pip install 'altk-evolve[pii]'` |

Read-cost note for `AccessStampPlugin`: fire-and-forget tasks are awaited before the sync bridge returns (their side effects would otherwise be lost with the closing event loop), so the stamp is **not** free for the reader — every public read pays one metadata write per returned entity before `search_entities` returns. Measured on the filesystem backend: ~3.7 ms vs ~0.1 ms for a 10-entity read; on milvus/postgres it adds N extra store round trips per read. Enable it only where access audit trails are worth that latency.

## Writing a plugin

A plugin is a `cpex.framework.Plugin` subclass whose async method names match the hook-type strings it subscribes to:

```python
from cpex.framework import Plugin
from cpex.framework.models import PluginConfig, PluginMode, PluginResult


class TagWrites(Plugin):
    def __init__(self, config: PluginConfig | None = None):
        super().__init__(config or PluginConfig(
            name="tag_writes",
            kind="my_pkg.plugins.TagWrites",
            hooks=["memory_pre_write"],
            mode=PluginMode.TRANSFORM,
        ))

    async def memory_pre_write(self, payload, context):
        entities = [
            {**e, "metadata": {**(e.get("metadata") or {}), "tenant": "acme"}}
            for e in payload.entities
        ]
        return PluginResult(
            continue_processing=True,
            modified_payload=payload.model_copy(update={"entities": entities}),
        )
```

Notes:

- Payloads are frozen — always `model_copy`, never mutate.
- To **block** an operation, return `PluginResult(continue_processing=False, violation=PluginViolation(...))`; the caller gets a `MemoryPolicyViolation`.
- Plugins that need to call back into the store (like `AccessStampPlugin`) can grab the live backend from `context.global_context.state["backend"]`.
- In tests, call `altk_evolve.hooks.shutdown_hooks()` between cases (the CPEX manager is a singleton).

## Deferred

- READI / semantic recall filtering plugins (separate branch).
- Lifecycle / retention policy hooks.
- A first-class PII configuration surface on `EvolveConfig` (today PII is configured through the plugin's own `config` block).
