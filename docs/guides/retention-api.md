# Retention interfaces

Python, CLI, REST, MCP, and the scheduler all call `RetentionService`. The service owns validation, namespace/agent scope, policy and rule operations, revision checks, schedule lifecycle, execution, and audit projections. The adapters supply authentication context and translate input/output formats.

`initiated_by` records who requested a run or schedule change. It is audit attribution, not a user-memory filter. Scheduled jobs preserve this identity when admitted. The REST adapter derives it from the authenticated user.

## Python

Use `client.retention(namespace_id, agent_id=...)`, or import `RetentionService` and `RetentionError` from `altk_evolve.retention`:

```python
from altk_evolve.frontend.client.evolve_client import EvolveClient

client = EvolveClient()
retention = client.retention("service-1", agent_id="agent-1")
retention.create_policy("memory-retention", name="Memory retention")
retention.add_rule(
    "memory-retention", "old-memories",
    {"max_age_days": 90, "action": "delete"},
)

schedule = retention.create_schedule(
    "nightly",
    {
        "policy_id": "memory-retention",
        "agent_id": "agent-1",
        "dry_run": True,
        "spec": {"schedule": "0 2 * * *", "timeZone": "America/Los_Angeles"},
    },
    initiated_by="alice",
)
shown = retention.get_schedule("nightly")  # configuration + next_runs
stopped = retention.stop_schedule(
    "nightly", initiated_by="alice", expected_revision=shown["revision"],
)
retention.start_schedule(
    "nightly", initiated_by="alice", expected_revision=stopped["revision"],
)
report = retention.run("memory-retention", initiated_by="alice")  # dry run
persisted = retention.get_run(report["run_id"])
```

Methods accept Python dictionaries and return Python dictionaries, not JSON strings. `RetentionError` exposes `status` (400 validation, 403 forbidden scope change, 404 missing, 409 conflict, or 500 execution failure) and `payload()`. Individual entity failures appear in the returned run report and persisted failed status; callers should inspect `errors` even when the overall request returns a report.

Policies are reusable namespace-wide definitions. Optional `agent_id` constrains schedules, jobs, and runs. Attempts to change a scoped schedule to another agent fail. `get_schedule` includes the next five nominal UTC times, or an empty list while suspended. `get_job` includes its persisted `run` when available.

## Operation mapping

REST paths below are relative to `/manage/retention` under the host's chosen mount prefix. MCP argument names match Python method parameters except for explicit `namespace_id` and, on rule updates, `changes`.

| Python method | REST | MCP tool |
| --- | --- | --- |
| `create_policy` | `POST /policies` | `create_retention_policy` |
| `get_policy` | `GET /policies/{id}` | `get_retention_policy` |
| `list_policies` | `GET /policies` | `list_retention_policies` |
| `update_policy` | `PATCH /policies/{id}` | `update_retention_policy` |
| `delete_policy` | `DELETE /policies/{id}` | `delete_retention_policy` |
| `add_rule` | `POST /policies/{id}/rules` | `add_retention_rule` |
| `list_rules` | `GET /policies/{id}/rules` | `list_retention_rules` |
| `update_rule` | `PATCH /policies/{id}/rules/{name}` | `update_retention_rule` |
| `remove_rule` | `DELETE /policies/{id}/rules/{name}` | `remove_retention_rule` |
| `create_schedule` | `POST /schedules` | `create_retention_schedule` |
| `get_schedule` | `GET /schedules/{id}` | `get_retention_schedule` |
| `list_schedules` | `GET /schedules` | `list_retention_schedules` |
| `update_schedule` | `PATCH /schedules/{id}` | `update_retention_schedule` |
| `delete_schedule` | `DELETE /schedules/{id}` | `delete_retention_schedule` |
| `start_schedule` | `POST /schedules/{id}/start` | `start_retention_schedule` |
| `stop_schedule` | `POST /schedules/{id}/stop` | `stop_retention_schedule` |
| `run` | `POST /runs` | `run_retention` |
| `get_run` | `GET /runs/{id}` | `get_retention_run` |
| `list_runs` | `GET /runs` | `list_retention_runs` |
| `get_job` | `GET /jobs/{id}` | `get_retention_job` |
| `list_jobs` | `GET /jobs` | `list_retention_jobs` |
| `cancel_job` | `POST /jobs/{id}/cancel` | `cancel_retention_job` |
| `recover_job` | `POST /jobs/{id}/acknowledge-interrupted` | `acknowledge_interrupted_retention_job` |

Full-document replacement is also available as `put_policy` / `PUT /policies/{id}` / `put_retention_policy` and `put_schedule` / `PUT /schedules/{id}` / `put_retention_schedule`. Schedule PUT creates only at revision zero and otherwise requires the observed revision. These use the same service as the explicit create/update operations. `validate_policy` and `validate_retention_policy` validate a complete policy without storage access.

## REST bodies

Create a policy:

```json
{"policy_id": "memory-retention", "name": "Memory retention", "enabled": true}
```

Add a rule at `/policies/memory-retention/rules`:

```json
{"name": "old-memories", "rule": {"max_age_days": 90, "action": "delete"}}
```

Update that rule at `/policies/memory-retention/rules/old-memories`:

```json
{"changes": {"max_age_days": 180}}
```

Create a schedule:

```json
{
  "schedule_id": "nightly",
  "definition": {
    "policy_id": "memory-retention",
    "agent_id": "agent-1",
    "dry_run": true,
    "spec": {"schedule": "0 2 * * *", "timeZone": "America/Los_Angeles"}
  }
}
```

PATCH schedule bodies contain `changes` and `expected_revision`. Start/stop bodies contain only `expected_revision`. DELETE supplies `expected_revision` as a query parameter. Run bodies contain `policy_id`, optional `dry_run`, and optional `additional_matches`. Recovery requires `{"worker_stopped": true}` after confirming that the owning process stopped.

Create/add requests return 201, reads and mutations return 200, malformed HTTP bodies return 422, and service failures use the service's status with `{"detail": {"error": "..."}}`. REST run responses and persisted audits omit memory content; programmatic/MCP immediate reports can include pre-action previews for trusted integrations.

The host injects an authenticated `MemoryScope`; namespace and initiator are not accepted from request bodies. Administrative routes require management permission. Python and MCP callers are trusted integrations and must authenticate and authorize their supplied namespace, initiator, and agent scope. See [embedding](embedded-memory-api.md).

## MCP inputs

New tools take structured objects. For example, `create_retention_schedule` receives `schedule_id`, `namespace_id`, `initiated_by`, and `definition` as an object. `update_retention_rule` receives an object in `changes`. Complete-policy/schedule PUT tools and `run_retention` also accept structured objects; their earlier JSON-string forms remain accepted by the transport adapter. Business operations never parse transport JSON.

MCP tools return JSON text; errors contain `error` and optional details. The separate preview tool/REST endpoint is removed: retrieve the schedule to see upcoming times.

## Runtime

Execution runs directly through the shared service, without invoking MCP from Python, REST, the CLI, or the scheduler. The `evolve-mcp` launcher owns background scheduling. Embedded hosts attach `retention_runtime(client)` to their lifespan; CLI catalog commands do not start background execution. See [scheduling](retention-scheduling.md).


## PostgreSQL collection

Applied PostgreSQL runs now mark candidates durably and sweep them in separate,
small transactions. `mark` alone never deletes a memory or invokes a hook. A
`flag` rule creates a review candidate; a `delete` rule creates a pending deletion.
Marking uses a persisted keyset cursor, so later runs advance past an already
processed page. Repeated marks of the same entity version and policy are idempotent.

```python
retention = client.retention("service-instance-id")
retention.mark("memory-retention", initiated_by="admin")
retention.list_candidates()
retention.sweep("memory-retention", initiated_by="admin")
retention.list_audit()
```

The matching CLI commands are `evolve retention mark POLICY`, `sweep POLICY`,
`candidates`, and `audit`; supply `--namespace` and, for mutations, `--initiated-by`.
REST adds POST `/manage/retention/policies/{id}/mark` and `/sweep`, plus GET
`/manage/retention/candidates` and `/audit`. MCP exposes `mark_retention`,
`sweep_retention`, `list_retention_candidates`, and `list_retention_audit`.
These use the same service-instance namespace; an administrator identity is attribution,
not a filter on memory owners. Omit agent scope for the entire service instance.

A sweep locks the candidate, policy, and current entity rows. Legal holds are
checked directly; a held memory remains marked. A changed entity version or policy
withdraws the old candidate for reevaluation by a later marking pass. Deletion and
its audit receipt commit together on the same PostgreSQL connection. No deletion
hook or external notification is part of this transaction. Ordinary non-retention
memory operations retain their existing hooks.

Candidate and audit APIs contain references, statuses, policy identifiers, rule
identifiers, timestamps, and operator attribution, never memory contents or titles.
Marks retain PostgreSQL row versions, not hashes of memory contents. Audit events
include a policy-definition hash identifying the applied revision. `missing` means
an entity was already unavailable; it is never reported as a confirmed deletion.
The UI resolves existing memories separately and groups committed outcomes; technical
references are expandable administrative details.

Scheduled PostgreSQL jobs have a 10-second heartbeat. A heartbeat older than 60
seconds permits the scheduler to mark the old run interrupted and admit future
occurrences. It never resumes that run. Already committed candidates and action
receipts survive. An old executor may overlap with a later run; idempotent marks
and transactional sweep claims make that overlap safe. Cancellation is checked
between candidates. A sweep already inside its transaction finishes or rolls back.

This atomic collection API requires PostgreSQL. Other backends retain the existing
immediate retention implementation. PostgreSQL applied runs use current policy
eligibility and namespace/agent scope: historical `as_of`, arbitrary metadata filters,
and externally computed deletion matches are rejected. CUGA's separate-database
orphan-conversation criterion is therefore not advertised by the new collection UI.
Dry-run evaluation remains available through `run(dry_run=True)`.
