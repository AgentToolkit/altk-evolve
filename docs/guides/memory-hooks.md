# Memory Hooks

`altk_evolve` exposes a general-purpose, pluggable hook seam around every memory operation and every LLM egress point. Plugins can normalize metadata, redact PII, audit access, filter recall results, or block operations entirely — without any change to Evolve's core code paths.

The seam — hook types, frozen payloads, dispatch points, veto semantics — is engine-agnostic, and so is plugin domain logic (pure core functions). Executing plugins requires an execution engine: a deliberately thin dispatch layer that Evolve keeps swappable. The engine integration shipped today is the [CPEX plugin framework](https://pypi.org/project/cpex/) — see [The CPEX engine](#the-cpex-engine) for everything specific to it.

## Motivation

Evolve's memory store sits between agents and durable state. Any cross-cutting memory policy wants to intercept that boundary:

- **Compliance** — redact PII before it is persisted or sent to an LLM.
- **Normalization** — stamp canonical metadata (e.g. `trace_id`, `created_at`) so downstream consumers can rely on it.
- **Access auditing** — record when memories were last recalled.
- **Recall filtering** — drop or transform results before they reach the caller.
- **Quality gates / cost controls** — reject low-value writes, cap or reshape LLM traffic.

Rather than baking each concern in, Evolve defines one extension seam with backend-layer choke points; any such policy plugs in as a hook plugin.

## The seam

- **Backend-layer choke points.** Write/read hooks fire inside `BaseEntityBackend` template methods, so no frontend (client, MCP server, CLI, Phoenix sync) can bypass them. Backends override protected `_*_impl` methods only; the public methods own hook dispatch.
- **Frozen payloads.** Each hook type carries an immutable pydantic payload. Plugins never mutate a payload in place; a transform proposes a replacement copy, transforms chain, and the final payload flows back to the call site. This is enforced, not just convention: mutable payload contents are deep-copied at dispatch, and an in-place mutation that isn't returned as a replacement payload is discarded.
- **Halting raises, never drops.** A plugin that halts the pipeline raises `altk_evolve.hooks.MemoryPolicyViolation` — a blocked write is an error the caller sees, not a silent no-op. (One deliberate exception: a vetoed conflict-resolution DELETE verdict skips that delete and lets the rest of the batch proceed — see *Unified delete semantics* below.)
- **Always live; behavior is which plugins you configure.** There is no master switch. With **no plugins** configured (empty config and nothing auto-discovered) every hook site is a fast no-op (a boolean check) that requires no execution engine and imports no `cpex` — byte-for-byte identical to a plugin-free install. With **plugins configured but the engine missing**, initialization fails **closed** with a clear error rather than silently no-opping a compliance plugin (see [The CPEX engine](#the-cpex-engine)).

### Hook taxonomy

| Hook type | Fires | Semantics | Payload |
|---|---|---|---|
| `memory_pre_write` | In `update_entities`, after namespace validation, **before** conflict resolution (so transforms run before content reaches an LLM) | transform / halt | `namespace_id`, `entities` (content + metadata dicts) |
| `memory_pre_metadata_patch` | Before `update_entity_metadata` merges a patch | transform / halt | `namespace_id`, `entity_id`, `metadata_patch` |
| `memory_pre_delete` | Before **every** entity delete — the public `delete_entity_by_id` and conflict-resolution DELETE verdicts inside `update_entities` | halt | `namespace_id`, `entity_id`, `metadata` (the stored entity's metadata; `None` if the entity was not found) |
| `memory_pre_namespace_delete` | Before `delete_namespace` | halt | `namespace_id` |
| `memory_post_read` | On public `search_entities` results only — internal reads (conflict-resolution pre-reads, the metadata-patch read-before-merge) never fire it | transform (filter/redact) / observe | `namespace_id`, `entities`, `query`, `filters` |
| `llm_pre_call` | Immediately before every litellm `completion` (fact extraction, guidelines, segmentation, clustering, conflict resolution) | transform (redact) / halt | `messages`, `purpose` (call-site tag), `model` |

Recursion safety: a `memory_post_read` plugin that patches metadata goes through `update_entity_metadata`, whose read-before-merge uses the internal `_search_entities_impl` seam — plus a context-local guard suppresses nested `memory_post_read` dispatch.

**Unified delete semantics.** Both delete initiators — the public `delete_entity_by_id` and LLM-issued DELETE verdicts from conflict resolution — route through a single guarded path (`BaseEntityBackend._guarded_delete`), so it is structurally impossible to delete an entity through the backend abstraction without `memory_pre_delete` firing. The payload carries the stored entity's `metadata` (fetched via the internal read seam on the public path; taken from the conflict-resolution pre-read on the verdict path), so policy plugins can key on fields like `legal_hold: true`. Veto behavior differs per caller: on `delete_entity_by_id` a halting plugin raises `MemoryPolicyViolation` to the caller; on a conflict-resolution DELETE verdict the veto skips *that* delete (the stored entity survives alongside its replacement), logs a warning, records the skip on the returned `EntityUpdate` (`event="NONE"` plus a `skipped_delete` metadata entry), and the rest of the batch still applies — a legal hold must not abort the whole write.

## Writing plugin logic

A plugin's domain logic is a **pure core function** — that is the plugin; everything else is engine adaptation. The in-tree plugins all follow this core/shim pattern:

1. **Pure core** — a plain function at module top level, no engine imports, operating on plain data (lists of dicts in, changed data or `None` out). It stays importable and unit-testable without any extra installed, so its tests are always-on CI coverage. Inject non-determinism (clocks, ids) as parameters.
2. **Thin engine shim** — an adapter class that subscribes the core to hook types on the execution engine. The shim only parses its configuration, calls the core, and wraps the result in the engine's result type. For the shipped CPEX engine that means a `cpex.framework.Plugin` subclass, defined under an `if engine_available():` guard, whose async method names match the hook-type strings it subscribes to.

```python
import datetime
from altk_evolve.hooks import engine_available


def tag_entities(entities: list[dict], *, tenant: str) -> list[dict] | None:
    """Pure core: returns tagged copies, or None when nothing changed."""
    if not entities:
        return None
    return [
        {**e, "metadata": {**(e.get("metadata") or {}), "tenant": tenant}}
        for e in entities
    ]


if engine_available():  # shim for the shipped CPEX engine
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
            cfg = self._config.config or {}
            entities = tag_entities(payload.entities, tenant=cfg.get("tenant", "acme"))
            if entities is None:
                return PluginResult(continue_processing=True)
            return PluginResult(
                continue_processing=True,
                modified_payload=payload.model_copy(update={"entities": entities}),
            )
```

See `altk_evolve/hooks/plugins/normalizer.py` (`normalize_entities`), `access_stamp.py` (`build_access_stamps`) and `readi.py` (`redact_spans` / `redact_entities` / `redact_messages`, with detection injected as a `SpanDetector` so the core is testable against a two-line fake) for shipped examples — their cores are importable without any extra installed. The one exception is `pii.py`: it is deliberately core-less, because adapting the external `cpex-pii-filter` plugin onto Evolve's hook types *is* its domain logic.

Notes:

- **Immutability contract — plugins MUST return `modified_payload`; mutating the payload in place is unsupported and can leak across a plugin chain.** Payloads are frozen (`model_copy`, never mutate), and payload contents are deep-copied once at dispatch to protect the *caller's* objects — but that copy does **not** isolate plugins from each other. If plugin A mutates its payload in place, plugin B later in the same chain receives A's mutation baked into B's input. The reviewer-suggested "deep-copy the returned payload" does **not** fix this (A's mutation is already in B's copy before B runs) — returning `modified_payload` is the only supported mechanism, and an in-place mutation that isn't returned is discarded.
- To **block** an operation, return `PluginResult(continue_processing=False, violation=PluginViolation(...))`; the caller gets a `MemoryPolicyViolation`. **A plugin that must be able to halt a write has to register in `sequential` mode.** CPEX silently downgrades `continue_processing=False` → `True` in `transform` (and `audit`) mode, so a `transform` plugin can redact or reshape but can **never** block — only `sequential` preserves *both* payload chaining and the ability to halt. This is why the shipped `PIIFilterMemoryPlugin` registers as `sequential` (so it can halt on unredactable PII), not `transform`.
- Plugins that need to call back into the store (like `AccessStampPlugin`) can grab the live backend from `context.global_context.state["backend"]`.
- **A plugin on a write hook (`memory_pre_write` / `memory_pre_delete` / `memory_pre_metadata_patch`) may call back for a metadata patch, but must not re-invoke `update_entities`.** `update_entity_metadata` is reentrant-safe from inside a write hook (RLock plus active-data reuse), so a write-hook plugin can patch metadata on the side. A nested `update_entities` is not: it reloads and nulls the active namespace buffer the outer write is still building, so the outer write is silently dropped. The write-family re-entrancy guard stops it from recursing infinitely, but does not save the outer write — do metadata work through the patch path, never a nested full write.

## Shipped plugins

| Plugin | Hooks | Mode | What it does |
|---|---|---|---|
| `MetadataNormalizerPlugin` | `memory_pre_write` | transform | Copies `task_id` → `trace_id` when only the former is present (MCP-saved trajectories vs Phoenix-synced ones) and stamps `created_at` |
| `AccessStampPlugin` | `memory_post_read` | fire_and_forget | Stamps `last_accessed` (ISO-8601 UTC) on read entities via the metadata-patch path |
| `PIIFilterMemoryPlugin` | `memory_pre_write`, `llm_pre_call` | sequential | Regex PII method (adapts the external `cpex-pii-filter` plugin onto Evolve's hook types); requires `pip install 'altk-evolve[pii-regex]'` |
| `ReadiSemanticPIIPlugin` | `memory_pre_write`, `llm_pre_call` | sequential | Semantic (NER) PII method via IBM READI — catches **names**, locations and organizations that regex cannot; requires `pip install 'altk-evolve[pii-semantic]'` |

**Two PII methods, run both.** Regex and semantic are two detection *methods*, not competing choices — the recommended default is to run **both** (regex for structured identifiers, semantic for names/entities), and enabling or disabling either is a YAML edit rather than a code change. It matters: measured on 200 rows of `ai4privacy/pii-masking-200k`, the regex method scores 0.13 overall span recall at precision 1.00 and **0.00 on first/last names**, while the semantic method scores 0.48 recall at precision 1.00 with names at 0.92-1.00 — the semantic method is the more powerful one, at the cost of being much slower and pulling model weights (~460MB). See the [PII redaction guide](pii-redaction.md) for the full numbers, model-choice guidance (language-matched spaCy pipelines), cost/latency trade-offs and limitations, and `examples/pii_benchmark.py` for the harness that produced them.

Read-cost note for `AccessStampPlugin`: fire-and-forget tasks are awaited before the sync bridge returns (see [The CPEX engine](#the-cpex-engine)), so the stamp is **not** free for the reader — every public read pays one metadata write per returned entity before `search_entities` returns. Measured on the filesystem backend: ~3.7 ms vs ~0.1 ms for a 10-entity read; on milvus/postgres it adds N extra store round trips per read. Enable it only where access audit trails are worth that latency. Its stamp is what makes `max_unused_days` retention rules meaningful — `EvolveClient.record_access` is the explicit equivalent for callers not running hooks, and both share the same `build_access_stamps` core. See [Data Retention](retention.md).

## The CPEX engine

Plugins need an execution engine to run. The engine layer is deliberately thin — one dispatch/manager module (`altk_evolve/hooks/manager.py`) between the choke points and the plugin runner. Hook types, payload classes, and plugin cores do not depend on it; swapping engines means reimplementing that dispatch layer, not rewriting plugins or the seam. The engine integration shipped today is **CPEX**, whose plugin manager provides plugin discovery, chaining, priorities, execution modes, and YAML configuration; the integration follows the intended-usage pattern the framework was designed for (a thin wrapper layer per host application). Everything in this section is specific to the CPEX path.

- **Optional dependency, fail-closed when configured.** `cpex` pulls heavy transitive dependencies (fastapi, mcp, prometheus), so it lives behind an extra: `pip install 'altk-evolve[hooks]'`. With **no plugins** configured every hook site is a fast no-op and cpex is never imported. Configuring a plugin **without** cpex installed raises `ImportError` with the install hint (fail-closed — configured plugins never silently degrade to a no-op). A configured plugin whose own detector lib is missing (e.g. READI without `[pii-semantic]`, or the regex filter without `[pii-regex]`) also surfaces its extra-naming `ImportError` at initialization, not lazily on the first write.
- **Execution modes and priorities.** Each plugin registers with a CPEX execution mode — `transform` (serial, chained, modifying, non-halting), `sequential` (may halt), `fire_and_forget` (side-effect only), `audit`, `concurrent`, `disabled` — a `priority` (lower runs earlier), and an `on_error` policy (`fail` / `ignore` / `disable`).
- **Fail-closed by default.** `on_error` defaults to `fail`: a plugin that crashes or times out halts the operation (a memory-write/`llm_pre_call` crash surfaces as `MemoryPolicyViolation`), rather than silently passing data through — the right default for a compliance plugin (e.g. PII redaction), but it trades availability for safety. A non-critical plugin (e.g. best-effort access auditing) can opt into `on_error="ignore"` so its failures don't block the operation. (A crash in a `memory_post_read` plugin never fails the read it rode in on — that hook is read-side transform-only and logs a warning instead.)
- **Sync bridge.** CPEX's `invoke_hook` is async-only; Evolve's call sites are sync. The seam uses `asyncio.run` when no event loop is running and a dedicated thread when one is. Fire-and-forget plugin tasks are awaited before the bridge returns so their side effects are never lost with the closing loop.
- **Singleton caveat.** CPEX's `PluginManager` is a process-wide (Borg) singleton — the hook seam is process-global, not per-client. Two sharp edges follow: (a) constructing a second `EvolveClient` whose config resolves plugins calls `PluginManager.reset()` and silently **replaces** the first client's plugins — for a compliance plugin (e.g. PII redaction) this means redaction can be silently disabled by unrelated code constructing its own client; (b) a client that resolves **no** plugins calls `shutdown_hooks()`, so it does not inherit another client's process-global plugins — no configured plugins truly means a no-op. Per-instance isolation (CPEX's `TenantPluginManager`) is deferred until a real use case needs it. In tests, call `altk_evolve.hooks.shutdown_hooks()` between cases.
- **PII adapter.** `PIIFilterMemoryPlugin` aliases the native `cpex-pii-filter` plugin onto Evolve's hook types; it needs the separate `[pii-regex]` extra (cpex + cpex-pii-filter; `[pii]` is a back-compat alias for it). `ReadiSemanticPIIPlugin` is a plain core/shim plugin (no cpex plugin to adapt) and needs the `[pii-semantic]` extra.

## Configuring plugins

The seam is always live; you turn behavior on by **configuring plugins**. There is no enable flag — a `HooksConfig` that resolves no plugins is a zero-cost no-op, and any configured plugin activates the seam (and requires the `[hooks]` engine, else init fails closed).

### Turnkey: `evolve hooks init`

The fastest path scaffolds a project-local config:

```console
$ evolve hooks init            # writes ./evolve.hooks.yaml
```

The scaffolded file ships the **READI semantic PII plugin active** and the **regex PII plugin commented out** (both `mode: sequential`, `on_error: fail`), with comments explaining each method and how to switch. Evolve **auto-discovers** it — no further wiring. Install the engine + detector to make it live: `pip install 'altk-evolve[pii-semantic]'` (see the [PII redaction guide](pii-redaction.md), including the macOS/MPS caveat).

### Auto-discovery search order

When `HooksConfig.plugins_yaml` is not set explicitly and no code-first `plugins` are given, Evolve searches for a default hooks config file and loads the **first** that exists:

1. `$EVOLVE_HOOKS_CONFIG` — an explicit path (an env override always wins).
2. `./evolve.hooks.yaml` — project-local, relative to the current working directory.
3. `~/.config/evolve/hooks.yaml` (or `$XDG_CONFIG_HOME/evolve/hooks.yaml`) — a per-user config.

An explicit `plugins_yaml` (or any code-first `plugins`) **overrides** discovery. Discovery finding nothing → no plugins → no-op.

### In code

```python
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.hooks import HookPluginSpec, HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient

config = EvolveConfig(
    hooks=HooksConfig(
        # Either point at an engine plugins.yaml (CPEX format)...
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

## Known limitations

- **`delete_namespace` does not fan out to `memory_pre_delete`.** Dropping a namespace fires **only** `memory_pre_namespace_delete`, never a per-entity `memory_pre_delete` for the entities inside it (fanning out would require an unbounded scan of the namespace). Consequence: a legal-hold plugin that vetoes deletes on `memory_pre_delete` does **not** protect entities removed by a namespace delete — they are dropped wholesale. A policy that must guard against that has to subscribe to `memory_pre_namespace_delete` and veto (or scope) the whole-namespace delete itself.

## Deferred

- READI / semantic recall filtering plugins (separate branch).
- Lifecycle / retention policy *hooks*. Data retention itself now ships as a policy-driven sweep rather than a hook — see [Data Retention](retention.md), which consumes `AccessStampPlugin`'s `last_accessed` stamp and `MetadataNormalizerPlugin`'s `trace_id` normalization.
- A first-class PII configuration surface on `EvolveConfig` (today PII is configured through the plugin's own `config` block).
- Additional execution engines: only the CPEX integration exists today; the seam is engine-agnostic, but running plugins currently requires cpex.
