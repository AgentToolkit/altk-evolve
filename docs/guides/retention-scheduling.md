# Retention scheduling

Evolve owns retention policies, schedules, job claims, execution, and run history. Hosts can manage them through MCP or the [embedded REST router](embedded-memory-api.md), without maintaining their own retention tables.

Create a policy first, then call `put_retention_schedule` with an explicit `namespace_id` (the host service-instance ID), `actor_id` (the authenticated operator), `schedule_id`, and a JSON-encoded `definition`:

```json
{
  "policy_id": "standard-retention",
  "agent_id": "agent-1",
  "dry_run": true,
  "spec": {
    "schedule": "0 2 * * *",
    "timeZone": "America/Los_Angeles",
    "concurrencyPolicy": "Forbid",
    "startingDeadlineSeconds": 3600,
    "suspend": false
  }
}
```

Omit `agent_id` for the entire namespace. Scheduled retention is administrative: it can process multiple users within that scope, and records the operator separately as `actor_id`. Personal memory APIs still isolate by namespace and user. `dry_run` defaults to true; set it to false to apply the policy.

The `spec` uses the timing fields of [Kubernetes batch/v1 CronJobSpec](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/). It can later be copied into a CronJob alongside a host-provided `jobTemplate`. Evolve does not currently create Kubernetes resources.

| Field | Behavior |
| --- | --- |
| `schedule` | Required five-field cron. Supports ranges, lists, steps, month/day names, `?` as `*`, and calendar descriptors such as `@daily`. Seconds, embedded `TZ` directives, and extensions such as `@every`, `L`, and `#` are rejected. |
| `timeZone` | IANA name. Defaults explicitly to `Etc/UTC`, unlike Kubernetes' controller-local default. |
| `concurrencyPolicy` | `Allow` (default), `Forbid`, or `Replace`, applied per namespace/schedule. |
| `startingDeadlineSeconds` | Optional nonnegative deadline from scheduled time to actual job claim. Omitting it leaves no deadline. Very short deadlines can be missed with the default ten-second polling interval. |
| `suspend` | Stops new admission without cancelling existing jobs. Resuming retains the missed-occurrence window. |

Nonexistent spring-forward wall times are skipped; repeated fall-back times can run twice. Preview returns absolute UTC timestamps through `preview_retention_schedule`. More than 100 missed occurrences produces a persisted schedule condition; set a deadline or change the timing to reset the window. Evolve admits the most recent eligible occurrence rather than replaying every missed run.

`Forbid` waits while an earlier job is queued or running. `Replace` cancels queued work and requests cancellation of running work, then waits for acknowledgment before admitting the replacement. Cancellation is cooperative between mutations: it cannot interrupt a blocked backend call and cannot roll back completed changes. This differs from Kubernetes terminating a Job's pods.

## Manage retention from the CLI

All commands live under `evolve retention`. Catalog commands print JSON and require an explicit service-instance namespace. The CLI uses the configured backend credentials and is intended for trusted operators; `--actor` records attribution, not authentication.

Save the definition above as `schedule.json` and a retention policy as `policy.yaml`, then:

```bash
# Store the policy referenced by the schedule.
evolve retention policies put standard-retention --namespace service-1 --file policy.yaml

# Create and inspect a schedule.
evolve retention schedules create nightly --namespace service-1 --actor alice --file schedule.json
evolve retention schedules list --namespace service-1
evolve retention schedules get nightly --namespace service-1

# Preview timing without connecting to storage.
evolve retention schedules preview --file schedule.json --count 5

# Edit schedule.json, then replace the definition using its current revision.
evolve retention schedules update nightly --namespace service-1 --actor alice --file schedule.json --revision 1

# Delete only when no jobs are active, using the revision returned by update.
evolve retention schedules delete nightly --namespace service-1 --revision 2
```

Updates replace the full definition. To suspend a schedule or apply changes instead of a dry run, edit `spec.suspend` or `dry_run` in the file and update with the last observed revision. Duplicate creates and stale updates/deletes fail with a nonzero exit status. Use `policies get` and `policies list` to inspect the policy catalog; `policies put --disabled` disables a policy.

Inspect executions with `evolve retention jobs list --namespace service-1` and `jobs get JOB_ID --namespace service-1`; the latter includes the retention report when available. Use `jobs cancel JOB_ID --namespace service-1` to request cancellation. After confirming an interrupted job's owning worker stopped, use `jobs recover JOB_ID --namespace service-1 --worker-stopped`.

The existing `evolve retention run --policy policy.yaml service-1` performs an immediate dry-run sweep from a file; `--apply` enforces it. `evolve retention execute` runs stored schedules and uses their persisted dry-run settings.

## Run the worker

Use the same backend configuration and durable catalog as the API/MCP service:

```bash
uv run evolve retention execute --poll-seconds 10 --max-workers 1
```

The worker is explicit; starting a frontend does not start a scheduler. Multiple workers coordinate admission and claims through database transactions. Filesystem deployments must also share the entity data directory, not just the catalog. PostgreSQL deployments use their configured PostgreSQL database; other backends use the existing SQLite retention catalog (`EVOLVE_RETENTION_STORE_PATH` can override its path). SQLite must be on storage that supports its locking semantics.

For an externally invoked one-shot worker:

```bash
uv run evolve retention execute --once
```

This dispatches currently due schedules, drains queued jobs, then exits. It can itself be run by a Kubernetes CronJob; in that arrangement Evolve still evaluates the stored schedules. A future per-schedule CronJob translation should use a separate execution entry point, avoiding two independent timing controllers for the same occurrence.

## Updates and recovery

Use `expected_revision: 0` to create, then the returned revision to update or delete. Updates affect future admissions; queued jobs retain the admitted definition and actor. The referenced policy is resolved at execution time, so policy edits or disabling take effect before execution. Changing the cron expression or timezone resets the scheduling cursor. Deletion requires no active jobs; suspend and cancel them first.

`list_retention_jobs` exposes ownership, status, heartbeat, and scheduled time. A started job's ID is also its retention run ID. Use `list_retention_runs` for persisted reports. Cancel with `cancel_retention_job`.

A crashed worker's destructive job is never automatically retried. Confirm that its owning worker has stopped, then call `acknowledge_interrupted_retention_job` with `worker_stopped: true`. This marks the job and any unfinished run interrupted, allowing later occurrences to proceed. A stale heartbeat alone is not proof that the worker stopped. Partial effects may exist; cancellation and recovery do not restore deleted entities.

Scheduled runs evaluate Evolve policy rules. Criteria owned by another application, such as whether a CUGA conversation still exists, require the host to evaluate them and supply `additional_matches` to a manual run; the scheduler does not query host databases.
