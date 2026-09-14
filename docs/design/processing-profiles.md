# Pluggable trajectory processing and runtime profiles

This PR implements processor registration/discovery, immutable execution plans,
versioned profiles, and Python/REST/MCP/CLI adapters. Profile processing is opt-in on
existing ingestion paths; calls without a profile retain their legacy behavior.

## Why profiles are separate from namespaces

Our application selects standard or consistency guidelines per namespace. Other
applications select behavior per agent, user, or request. Third-party processing may
have nothing to do with guidelines.

A profile selects processor plugins and their configuration. The application decides
which profile applies. A namespace remains a persistence destination, not a mandatory
configuration ownership model. The core never needs a new field for third-party
configuration.

## Define processing with real built-in settings

```json
{
  "schema_version": 1,
  "processors": [
    {
      "id": "guidelines",
      "plugin": "evolve.guidelines",
      "config": {
        "guidelines_mode": "consistency",
        "consistency_method": "accurate",
        "segmentation_enabled": false
      }
    }
  ]
}
```

The built-in adapts the existing standard, fast consistency, and accurate consistency
implementations. `guidelines_mode` accepts `standard`, `consistency`, or `all`;
`consistency_method` accepts `fast` or `accurate`. Its schema also includes the
model/provider, segmentation, and optional accurate-analysis configuration. Omitted
values are resolved when validating/publishing, not read repeatedly during processing.
An omitted analysis configuration is captured from the shipped analyzer YAML.
An explicit analysis configuration replaces it; it is not a path to a file that can
change under a running job. Debug-output location remains deployment-controlled.

Each instance ID is unique within the profile. Multiple instances may use the same
plugin. The processor list is ordered; an empty list explicitly runs no processors.
Updates replace the definition, so removed config fields return to plugin defaults.
Plugin validation runs before publication. Invalid modes do not silently fall back.

## Python: no profile persistence required

```python
from altk_evolve.processing import ProcessingService

processing = ProcessingService()  # built-ins + installed entry points; in-memory profiles
plan = processing.validate(definition)
result = processing.process({"messages": messages}, plan=plan)
# result.entities contains proposed entities; this form performs no database writes.
```

To persist outputs through the existing backend and memory hooks:

```python
from altk_evolve.frontend.client.evolve_client import EvolveClient

client = EvolveClient(processing=processing)
client.ensure_namespace("memories")
result = client.process_trajectory(
    {"messages": messages, "trace_id": "task-123"},
    namespace_id="memories",
    plan=plan,
)
```

Without an explicit service, `EvolveClient.processing` lazily creates a service with
a SQLite profile repository. Its path is `EVOLVE_PROCESSING_PROFILES_PATH`, falling
back to `EVOLVE_SQLITE_PATH`, `EVOLVE_SQLITE_URI`, then `entities.sqlite.db`.
An injected `ProfileRepository` can use application storage instead. The profile
repository is independent of the entity backend; using Postgres entities does not
implicitly put profiles in Postgres.

## Python: saved profiles and application selection

```python
from altk_evolve.processing import ProfileReference

created = client.processing.put("support-review", definition, expected_revision=0)
updated = client.processing.put("support-review", new_definition, expected_revision=created["revision"])
plan = client.processing.resolve("support-review", revision=updated["revision"])
result = client.process_trajectory(trajectory, namespace_id="memories", plan=plan)
```

Revision 0 means create-only. Subsequent writes require the last observed revision;
stale writes raise `ProfileConflict`. Old revisions remain available. SQLite updates
use a transaction and revision check, including across repository instances.

An application can choose profiles directly or inject a selector:

```python
client = EvolveClient(
    processing=processing,
    processing_selector=lambda context: ProfileReference(id=context["agent_profile"]),
)
result = client.process_trajectory(
    trajectory,
    namespace_id="memories",
    context={"agent_profile": "support-review"},
)
```

The selector may return a `ProcessingPlan`, a profile name, a `ProfileReference`, or
None. None means use the compatibility default built-in plan. An explicit plan or
profile bypasses the selector. Unknown references are errors, not default fallback.
The context is application-owned; no user/agent hierarchy or authentication is inferred.

Namespace, user, and agent profile bindings belong to the embedding application.
For our application's namespace metadata, a binding could be
`{"processing_profile": {"id": "support-review"}}`; this PR does not add a namespace
metadata column or a namespace-specific binding API. It provides the selection seam
without imposing a storage model on other applications.

## Runtime switching and pinned jobs

```python
# Latest: refresh before each trajectory.
for trajectory in trajectories:
    client.process_trajectory(
        trajectory, namespace_id="memories", processing_profile="support-review"
    )

# Pinned: resolve once, including defaults and implementation references.
plan = client.processing.resolve("support-review", revision=1)
for trajectory in trajectories:
    client.process_trajectory(trajectory, namespace_id="memories", plan=plan)
```

If A starts under revision 1 and an update publishes revision 2, A finishes with
revision 1; the next latest-following trajectory uses revision 2. This applies to
processor additions/removals as well as settings. Plans capture processor classes in an ordered tuple and store resolved configuration
once in the serialized manifest. Each invocation validates an isolated config and
calls the class's `from_config(config)` factory to create a fresh processor instance;
plugins receive isolated trajectory copies.

Profiles store normalized defaults and plugin versions. Resolving a saved profile
rejects incompatible installed versions or configuration drift; publish a new revision
explicitly after upgrades. A retained in-process plan keeps its processor class references.
Hot replacement of installed Python code is unsupported.

## Built-ins, discovered packages, and local plugins

A processor class has `id`, `api_version=1`, `version`, a Pydantic `config_model`,
a `from_config(config)` classmethod that constructs its configured instance, and a
`process(trajectory, *, context)` instance method returning `ProcessorResult`.
Config schemas are plugin-owned. The result contains entities, diagnostics, and an
optional request for persistence-time conflict resolution. No subclass is required. Registration and inventory inspect class metadata and never
construct instances. Construction belongs to the plugin, so its constructor can
require configuration or plugin-specific dependencies:

```python
class MyProcessor:
    id = "example.custom"
    api_version = 1
    version = "1.0"
    config_model = MyConfig

    @classmethod
    def from_config(cls, config):
        return cls(MyConfig.model_validate(config))

    def __init__(self, config):
        self.config = config

    def process(self, trajectory, *, context):
        return ProcessorResult(entities=[])
```

`MyConfig` is the application's Pydantic model. The runner supplies a fresh validated
config to `from_config` for every trajectory, including repeated runs of a pinned
plan. There is no separate `BoundProcessor` record or external factory callable.

Built-ins are registered automatically. Installed packages advertise entry points:

```toml
[project.entry-points."altk_evolve.processors"]
"example.word_count" = "word_count:WordCountProcessor"
```

Discovery enumerates registrations without running processors. Schemas/implementations
are loaded on demand. This uses the standard
[Python entry-point mechanism](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/).
Duplicate IDs (including built-in shadowing), incompatible APIs, and missing selected
plugins fail explicitly. Inventory reports unavailable plugins. Restart after installing
new packages. Python plugins execute trusted code in the host; profiles cannot install
packages or supply arbitrary import paths.

Local plugins use the same registry:

```python
from altk_evolve.processing import ProcessorRegistry, ProcessingService

registry = ProcessorRegistry.discover()
registry.register(MyProcessor)
processing = ProcessingService(registry=registry)
```

Use `discover(installed=False)` to exclude installed extensions, or
`ProcessorRegistry()` for an empty registry. Registration makes a processor available;
only a plan/profile activates it.

A complete no-LLM package example is in
[examples/processing_plugin](../../examples/processing_plugin/pyproject.toml).
Install it into the same environment as the CLI, then run:

```sh
uv pip install --no-deps -e examples/processing_plugin
uv run evolve processors list
uv run evolve processing-profiles apply word-count --file examples/processing_plugin/profile.json --expected-revision 0
uv run evolve namespaces create memories
uv run evolve processing run --file examples/processing_plugin/trajectory.json --namespace memories --processing-profile word-count
```

## REST and MCP

REST is mounted under `/api` on the existing FastAPI service:

| Action | REST | MCP tool |
| --- | --- | --- |
| IDs, versions, JSON config schemas | `GET /api/processors` | `list_processors` |
| Read profile/latest or pinned revision | `GET /api/processing-profiles/{id}?revision=1` | `get_processing_profile(profile_id, revision=None)` |
| Validate and replace profile | `PUT /api/processing-profiles/{id}` | `set_processing_profile(profile_id, definition, expected_revision)` |
| Run processors and persist derived entities | `POST /api/trajectories` | `process_trajectory(trajectory, namespace_id, processing_profile, revision=None)` |

PUT takes the complete definition above. Create with `If-None-Match: *`; update with
`If-Match: "1"`. Responses include ETag and revision. Missing write preconditions
return 428, stale writes 409, missing profiles 404, and invalid config 422.
MCP uses `expected_revision=0` for create-only.

POST body:

```json
{
  "namespace_id": "memories",
  "processing_profile": {"id": "support-review", "revision": 1},
  "trajectory": {
    "trace_id": "task-123",
    "messages": [{"role": "user", "content": "Review this task"}]
  }
}
```

Omit revision to select latest. The destination namespace must already exist.
These execution APIs persist derived entities, not the raw trajectory. Existing
MCP `save_trajectory` also accepts `processing_profile` and optional
`profile_revision`; it preserves its raw-trajectory persistence and return shape.
The service's existing authentication model is unchanged. Application adapters must
supply their own authorization; identifiers alone are not authenticated principals.
The stock REST/MCP service has no authenticated-principal or profile-editor policy.
Run it within a trusted deployment boundary, or protect all routes with application
authentication and resource authorization before exposing it to untrusted callers.

## CLI and Phoenix sync

CLI profile operations use the configured local repository, not an implicit remote
service. Use REST/MCP to manage a remote process's repository.

```sh
uv run evolve processing-profiles get support-review --revision 1
uv run evolve sync phoenix --processing-profile support-review
uv run evolve sync phoenix --processing-profile support-review --profile-revision 1
```

Without a revision, Phoenix sync resolves before each trajectory; a pinned revision
is resolved once when the syncer is constructed. A profile cannot be combined with
legacy `--guidelines-mode` or `--consistency-method` flags. Existing invocations without
a profile remain compatible. Profiles are activated explicitly, so installing an
extension does not change existing sync behavior.

## Persistence, hooks, and failure behavior

Processors execute sequentially against the same original input, without consuming
each other's results. All must succeed before derived writes begin. Failures stop the
profile operation rather than silently discarding a processor's output. This differs
intentionally from the legacy `all` mode's best-effort consistency branch.

Persistence groups outputs by entity type and uses the normal backend path. Existing
memory hooks still run; built-in generation preserves LLM-egress hooks. Third parties
can use `context.complete(...)` for mediated LLM calls. Arbitrary direct network calls
from trusted third-party Python cannot be intercepted by this contract.

The runner captures conflict-resolution model/provider settings with the plan and
passes them through the client/backend/LLM call chain. Generation receives explicit
settings; it does not mutate globals to switch modes. Per-call LiteLLM global JSON
validation toggles were replaced with per-request validation in these generation paths;
responses are also locally validated with Pydantic. Deployment connections, credentials, and global hook setup
are not hot-swapped by profiles. Constructing clients with different hook configurations
still has the existing process-global hook lifecycle limitation.

Every produced entity carries the full effective manifest, digest, operation ID,
processor instance ID, and optional profile ID/revision. This duplicates small manifests
rather than requiring a separate artifact store. Provenance is stamped after conflict
resolution so model-returned metadata cannot replace it. Unchanged entities retain
prior provenance. Results expose proposed entities and actual persistence updates
separately; an update may consolidate into an existing entity.

No transaction spans all processor outputs or backends. A storage failure can leave
partial writes; retry/idempotency orchestration is not implemented. MCP ingestion
retains its existing early raw writes; Phoenix profile execution marks a trajectory
processed only after derived processing/persistence succeeds. Reuse a captured plan
for application-managed retries. Effective settings are inspectable, but model outputs
are not deterministic. Keep credentials out of profile config; inject deployment
resources instead.

## Validation and remaining scope

Unit tests cover revision conflicts, restart persistence, plugin validation/discovery,
mode switching during processing, pinned plans, isolated configs, conflict provenance,
application selectors, shared transport APIs, and existing ingestion compatibility.
A subprocess E2E test discovers a distribution entry point, updates profiles through
separate CLI invocations, and verifies pinned/latest results in filesystem storage.
Existing guideline, consistency, backend, hook, and CLI tests remain regression gates.

Not implemented: namespace binding storage, remote CLI management, execution DAGs,
parallel processors, plugin hot code reload, automatic retries/cancellation, and
multi-worker guarantees for entity backends. SQLite profile publication itself uses
atomic revision checks. A caller may inject another profile repository; none of the
processing interfaces require namespace-specific SQL or a fixed user/agent model.
