# Proposal: pluggable trajectory processing and runtime profiles

Status: draft for discussion. This is a design-only proposal, not an implemented
API. All processor identifiers, profile APIs, and commands below are illustrative.
The existing guideline configuration fields and values are real.

## Problem and decision

A service needs to change trajectory processing while it is running. In our
application, a namespace chooses standard or consistency guidelines. Other
applications may select behavior per agent, per user, or per request, and third-party
processors may perform work unrelated to guidelines.

Make the configurable unit a **processor plugin**. An immutable processing plan
contains the selected processors and their validated configurations. A named,
versioned **processing profile** is an optional way to persist and select that plan.
The application decides which profile applies. Evolve does not impose a namespace,
agent, or user hierarchy on processing.

Built-ins, installed extensions, and application-local processors use one contract
and registry. Python, REST, MCP, and CLI call the same validation, profile-management,
and execution services. No first-party-only `RuntimePolicy` union is required.

## What exists on remote main

This proposal was checked against `origin/main` at `b95ed43` on 2026-09-11.

| Existing code | Relevant behavior |
| --- | --- |
| [GuidelinesSettings](../../altk_evolve/config/guidelines.py) | `guidelines_mode`: `standard`, `consistency`, `all`; `consistency_method`: `fast`, `accurate`. Defaults are `standard` and `fast`. |
| [MCP ingestion](../../altk_evolve/frontend/mcp/mcp_server.py) | `save_trajectory` reads settings and branches into standard and/or consistency generation. |
| [Phoenix sync](../../altk_evolve/sync/phoenix_sync.py) | Repeats the generation selection while processing trajectories. |
| [Consistency implementation](../../altk_evolve/llm/guidelines/consistency_guidelines.py) | Separate fast and accurate generation paths already exist. |
| [Memory hook contract](../../altk_evolve/hooks/plugin.py) | Native `HookPlugin` methods intercept memory operations and LLM egress. |
| [Hook configuration](../../altk_evolve/config/hooks.py) | Existing file discovery and programmatic `HookPluginSpec` registration. This is not yet processor entry-point discovery. |
| [Namespace schema](../../altk_evolve/schema/core.py) | No namespace metadata field yet. |
| [SQLite namespace store](../../altk_evolve/db/sqlite_manager.py) | Namespace rows contain `id` and `created_at`; both Milvus and Postgres use this store. Filesystem namespaces use JSON. |

The local database inspected during design contained `{ "id": "evolve",
"created_at": 1775234663 }`. It did not contain processing configuration.

## One profile, first-party and third-party behavior

A profile definition selects processors in execution order:

```json
{
  "schema_version": 1,
  "processors": [
    {
      "id": "guidelines",
      "plugin": "evolve.guidelines",
      "config": {
        "guidelines_mode": "consistency",
        "consistency_method": "accurate"
      }
    },
    {
      "id": "quality-review",
      "plugin": "acme.quality_review",
      "config": {
        "rubric": "customer-support",
        "minimum_score": 0.8
      }
    }
  ]
}
```

`evolve.guidelines` adapts existing generation behavior. The Acme processor and its
settings are hypothetical third-party examples. The core knows neither schema.
Each processor owns its config schema, defaults, validation, and implementation.
`id` is a unique instance identifier within the profile, allowing multiple differently
configured instances of one plugin. Omit a processor to disable it. An empty list
explicitly performs no derived processing; it is not a request for default plugins.

The schema is an envelope, not an unrestricted import mechanism. A profile names
registered plugins; it cannot specify an arbitrary Python import path or install a
package. JSON configuration contains no executable code or credentials. Inject
credentials and other deployment resources through runtime services.

## Processor contract and existing hooks

A processor descriptor supplies a stable identifier, plugin API version, config
schema version, implementation/package version, and a configuration model exposing
JSON Schema. Conceptually, the execution contract is:

```python
class TrajectoryProcessor(Protocol):
    def process(self, trajectory, *, config, context) -> ProcessorResult: ...
```

This is pseudocode; concrete trajectory and result types are part of implementation.
The runner normalizes input, including messages, tool schemas, and trace identifiers,
so third-party processors do not have to understand every transport's request shape.
`ProcessorResult` contains proposed entities and structured diagnostics. `context`
provides an operation ID and injected services, including mediated LLM access.
Neither context nor the processor config requires an agent/user/namespace ownership
model. The persistence destination is separate from profile selection.

For v1, processors run sequentially in listed order, each consuming the same original
trajectory, not another processor's output. Each gets immutable input or an isolated
copy and an operation-local instance. There is no cross-operation mutable config.
Any processor failure stops derived processing; collect results before derived writes.
An empty successful result is valid. Dependency graphs, parallel scheduling, and
processor-managed external side effects are deferred. Third-party packages execute
trusted Python in the host process; this is not a sandbox.

The runner persists outputs through the existing client/backend path. Memory hooks
remain attached to memory operations; existing LLM-egress hooks remain attached to
LLM calls. Processor adapters must preserve these dispatches. Plugins using the
provided LLM service get mediated egress; arbitrary direct network calls from trusted
Python cannot be guaranteed to pass through those hooks.

A trajectory processor produces derived results. A memory hook intercepts operations.
These are distinct responsibilities; a package can provide both. Do not replace
`HookPlugin`, reinterpret hook YAML as profiles, or execute the same plugin twice.
Global deployment hooks remain outside per-profile control in this first proposal.

Built-in adapters must eliminate mid-operation reads of mutable generation settings.
Capture model/provider/segmentation choices, the accurate method's analysis config,
and persistence-time conflict-resolution settings before processing. Thread captured
values through `EvolveClient.update_entities` → `BaseEntityBackend.update_entities`
→ `resolve_conflicts`. Backend connections remain deployment infrastructure. Audit
shared LiteLLM flags and hook lifecycle state during implementation; wrapping current
functions without addressing global reads does not provide snapshot consistency.

## Registration and discovery

Normal startup registers built-ins and discovers installed entry-point descriptors.
Applications can add local processors explicitly:

```python
registry = ProcessorRegistry.discover(include_builtins=True)
registry.register(MyLocalProcessor)
client = EvolveClient(processors=registry)
```

These are proposed names. `EvolveClient()` would use the default registry automatically;
applications may supply a restricted registry instead. Built-ins are available by
default; a compatibility profile, not registration itself, preserves current activation.

An installed package advertises a processor using Python packaging entry points:

```toml
[project.entry-points."altk_evolve.processors"]
"acme.quality_review" = "acme_review:QualityReviewProcessor"
```

Discover with `importlib.metadata.entry_points(group="altk_evolve.processors")`;
load a descriptor when its schema or implementation is needed. This follows the
[PyPA plugin discovery mechanism](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/).
Loading imports Python code, so discovery is not remote installation.

Reject duplicate identifiers, including attempts to shadow built-ins. Validate API
compatibility and config schema versions. Unknown or unloadable selected plugins
fail profile validation and execution explicitly. Inventory reports unavailable
plugins without silently dropping them or breaking unrelated inventory entries.
Require restart to discover newly installed packages in v1. Plans retain captured
implementations; changing installed code during execution is unsupported.

## Profile persistence and application-owned selection

Use a `ProfileRepository` interface for immutable revisions and an atomic current
revision pointer. A create uses create-only semantics; an update requires the last
observed revision. Validate the entire candidate and publish it transactionally.
Never partially apply a processor list or merge arrays by position. Profiles use
whole-definition replacement; removing a config key restores that plugin's default
when resolving the new plan.

Persistence implementations may use SQLite, Postgres, or application storage. An
in-memory repository supports library use and tests. Neither the core nor REST/MCP
handlers issue namespace-specific SQL. Preserve prior revisions for pinned jobs.
Store declarations with plugin/config schema versions; resolving captures all
otherwise inherited defaults as well as implementation versions. A profile revision
alone does not identify effective behavior across changed deployments.

The application supplies a selector, or the caller supplies a profile reference:

```python
profile_ref = application.select_profile(context)
plan = profiles.resolve(profile_ref)
```

Our application can associate the real namespace row with a profile by adding a
metadata field (proposed storage extension):

```json
{
  "id": "evolve",
  "created_at": 1775234663,
  "metadata": {
    "processing_profile": {"id": "support-review", "follow": "latest"}
  }
}
```

Namespaces are not shared in our application. Other applications may store this
reference on an agent or user record, or avoid persistent bindings entirely. Evolve
must not prescribe precedence between those sources. Association storage and profile
storage are separate interfaces; a generic core does not require an agent/user table.

A latest binding uses `{ "id": "support-review", "follow": "latest" }`.
A pinned binding uses `{ "id": "support-review", "revision": 4 }`.
Reject references specifying both. A shared profile update affects every latest
follower, even if their namespaces are separate; use separate profiles when independent
configuration is intended. Application adapters enforce access to bindings and profiles.
Caller-supplied context identifiers alone are not authenticated identity.

## One execution snapshot

Before each trajectory, resolve its selection, load one profile revision, validate
all processors, and freeze the complete effective plan. A plan captures processor
order, config, schema and implementation versions, and relevant execution-service
settings. Use immutable nested structures, not only a frozen outer model.

A latest-following batch reselects and resolves at each trajectory boundary. A pinned
batch resolves once and retains the resulting plan for the entire batch. A retry
reuses the captured plan, rather than resolving latest again. Persist its complete
non-secret effective manifest with the operation, with a digest referenced by produced
entities. Record failures and partial persistence against that operation. Referenced
manifests must be retained as long as their results are retained.

Example: trajectory A starts with profile revision 4 using `accurate`. During an LLM
call, an update publishes revision 5 selecting `fast`. A finishes, including conflict
resolution, with its old plan. Trajectory B resolves revision 5. Adding or removing
Acme's processor follows the same rule, without a first-party special case.

Provenance is attached after model-produced conflict-resolution output, so arbitrary
metadata cannot overwrite it. Retaining a manifest makes settings inspectable, not LLM
outputs deterministic. Replaying across deployments also requires compatible installed
plugin versions and resources; fail explicitly if these are unavailable.

No distributed transaction across plugin work and all entity backends is promised.
Existing raw-trajectory writes and partial persistence behavior must be documented.
Use operation IDs and explicit failure reporting; durable retries/idempotency,
cancellation/restart, and multi-worker deployment guarantees are subsequent work.

## Same services through four interfaces

All APIs below are proposed. Existing endpoints and commands remain compatible.

| Action | REST | MCP | CLI |
| --- | --- | --- | --- |
| Discover processor IDs, versions, schemas | `GET /processors` | `list_processors` | `evolve processors list` |
| Read profile and revision | `GET /processing-profiles/{id}` | `get_processing_profile` | `evolve processing-profiles get support-review` |
| Create or replace profile | `PUT /processing-profiles/{id}` | `set_processing_profile` | `evolve processing-profiles apply support-review --file profile.json` |
| Submit trajectory with a reference | `POST /trajectories` | optional `processing_profile` on `save_trajectory` | optional `--processing-profile` on sync |

REST updates use `If-Match: "4"`; creation uses `If-None-Match: *`. MCP and Python use
`expected_revision=4`, or `create_only=True`; CLI exposes corresponding flags. All
map to the same repository concurrency check. Stale updates return a conflict and
leave the active revision unchanged. Invalid configurations identify the processor
instance and field. Unknown profiles/plugins fail instead of falling back to defaults.

For example, PUT the definition above to `/processing-profiles/support-review`, then
submit a trajectory with `processing_profile: {"id": "support-review", "follow":
"latest"}`. An omitted reference invokes the application selector, falling back to
an explicit compatibility default only when no selection exists. An invalid selection
is an error, not absence. CLI service management needs an explicit server target;
local CLI operations use a configured local repository. They do not silently modify
an unrelated service's profiles.

Programmatic calls can use the same profile services:

```python
saved = client.processing_profiles.put(
    "support-review",
    definition=definition,
    expected_revision=4,
)
plan = client.processing_profiles.resolve("support-review", revision=saved.revision)
result = client.process_trajectory(trajectory, namespace_id="evolve", plan=plan)
```

Or skip profiles and persistence entirely:

```python
plan = client.processing.validate(definition)
result = client.process_trajectory(trajectory, namespace_id="evolve", plan=plan)
```

A long-running application can resolve a different profile for each user/agent:

```python
for trajectory in trajectories:
    reference = application.select_profile(trajectory.context)
    plan = client.processing_profiles.resolve(reference)
    client.process_trajectory(trajectory, namespace_id=destination, plan=plan)
```

The conceptual overloads above share one resolver. No public function accepts
`guidelines_mode` or an Acme-only argument; those belong to their plugins. Direct
plans still receive operation provenance without inventing a saved profile revision.

## Migration and reviewable implementation slices

1. Define processor descriptors, validation, registry discovery, and immutable plans.
   Add a fake third-party processor fixture proving no core schema changes are needed.
2. Adapt existing guideline generation to a built-in processor using real settings.
   Capture transitive dependencies and preserve memory/LLM hooks. A compatibility plan
   resolves existing file/env and constructor settings, including `standard`/`all`
   behavior. Keep existing public call signatures; do not introduce proxy globals.
3. Route MCP ingestion and Phoenix sync through the runner. Keep legacy persistence
   and error behavior explicit, then add opt-in profile execution. Test parity before
   activating the new path by default.
4. Add repository revision publication, manifest storage, and an optional namespace
   binding adapter. Backend storage choices stay behind interfaces; migrate old
   namespace records with empty metadata defaults. Application-owned user/agent
   selectors need no core schema migration.
5. Add REST/MCP/CLI profile management and processor inventory over shared services.
   Preserve existing deployment hooks and authorization boundaries.

Required tests include mode and plugin-list switching while an operation is blocked;
no change inside the old operation; pinned batches and retries; failed/stale updates;
unknown plugins and incompatible versions; duplicate registrations; discovery without
activation; third-party config rejection; cross-user/agent isolation with a shared
storage namespace; namespace-only selection; persistence across restart; provenance
after conflict resolution; and compatibility with existing LLM/memory hooks.

Open decisions: the first concrete result/diagnostic types, resource lifecycle and
cleanup APIs, and which repository ships first. Per-processor error policies, DAGs,
remote installation, hot code reload, and profile-controlled deployment hooks are
outside the initial proposal. No production API or database migration is introduced
by this documentation PR.
