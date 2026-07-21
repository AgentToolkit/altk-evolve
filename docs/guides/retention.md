# Data Retention

Memories accumulate. Some go stale, some are never read again, and some — session transcripts especially — carry data you agreed to keep for a bounded time. `altk_evolve.retention` applies a declarative policy to a namespace: it selects entities by type and age, then either **flags** them for review or **deletes** them, and can cascade a session delete to the memories derived from that session.

It is a sweep, not an interceptor: you run it (CLI, cron, or in code), it reports what it would do, and it only mutates when you say so. **Dry run is the default everywhere.**

## Quick start

```bash
evolve retention run --policy examples/retention.example.yaml            # dry run: report only
evolve retention run --policy examples/retention.example.yaml --apply    # enforce
```

Or in code:

```python
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.retention import RetentionEngine, RetentionPolicy

policy = RetentionPolicy.from_file("retention.yaml")
report = RetentionEngine(EvolveClient()).apply("my-namespace", policy)  # dry_run=True by default
print(report.summary())
for item in [*report.deleted, *report.flagged]:
    print(item.action, item.entity_id, item.reason, "—", item.detail)
```

[`examples/retention_demo.py`](https://github.com/AgentToolkit/altk-evolve/blob/main/examples/retention_demo.py) is a runnable end-to-end walkthrough.

## Policy format

A policy is an ordered list of rules, in YAML or JSON:

```yaml
rules:
  - name: unused-guidelines
    entity_type: guideline
    max_unused_days: 180
    action: delete
    on_missing_access_signal: skip

  - name: stale-guidelines
    entity_type: guideline
    max_age_days: 90
    action: flag

  - name: old-sessions
    entity_type: trajectory
    max_age_days: 365
    action: delete
    cascade_derived: true
```

| Field | Meaning |
|---|---|
| `name` | Required. Surfaced in every report line, so make it explain itself. |
| `entity_type` | Only match this type (`guideline`, `trajectory`, …). Omit to match every type. |
| `max_age_days` | Match entities whose `created_at` is older than this. |
| `max_unused_days` | Match entities not read in this many days (see [The unused signal](#the-unused-signal)). |
| `action` | `flag` (default) or `delete`. |
| `on_missing_access_signal` | For a `delete` rule: what to do when an `unused` match has no real `last_accessed` stamp. `skip` (default), `flag`, or `delete` — see [When the unused signal is missing](#when-the-unused-signal-is-missing). |
| `cascade_derived` | On a `delete` of a session entity, also delete the memories derived from it. |

Every rule must set `max_age_days` and/or `max_unused_days` — a rule with no threshold is a config error, not a match-everything rule.

**Rules are evaluated top-to-bottom and the first match wins per entity**, so put narrow rules first. One exception, deliberately: a cascade `delete` supersedes a `flag` an earlier rule had already assigned to the same entity. Delete always wins over flag; otherwise the first writer wins.

### First-match shadowing

Because the first matching rule wins and never re-evaluates, an earlier broad rule can **shadow** a later one so it can never fire. The classic trap is ordering a short-threshold `flag` before a long-threshold `delete` on the same type:

```yaml
# WRONG — the delete is unreachable
- name: stale-guidelines
  entity_type: guideline
  max_age_days: 90        # any 180-day-unused guideline is also >90 days old…
  action: flag
- name: unused-guidelines
  entity_type: guideline
  max_unused_days: 180    # …so this rule never wins: the flag above always matches first
  action: delete
```

Any guideline unused for 180 days is necessarily more than 90 days old, so the `flag` rule always matches it first and the `delete` never runs. Order is **escalation, not accumulation**: put the more aggressive / longer-threshold rule first, so the safety-net `flag` catches only what the `delete` didn't. `examples/retention.example.yaml` ships the corrected ordering.

## Flag vs delete

- **`flag`** is non-destructive. It merges three keys into the entity's metadata — `retention_flagged_at`, `retention_reason`, `retention_rule` — and leaves content untouched. Use it to build a review queue, or as a soft first stage before a later delete rule.
- **`delete`** removes the entity through `EvolveClient.delete_entity_by_id`. There is no undo.

Because deletes go through the client's public API, they flow through the `memory_pre_delete` hook — so a legal-hold plugin can veto a retention delete (see [Memory Hooks](memory-hooks.md)). A vetoed delete surfaces as an entry in `RetentionReport.errors`; the sweep continues with the remaining entities rather than aborting.

## Dry run by default, and how to apply

`RetentionEngine.apply()` defaults to `dry_run=True`, and the CLI requires an explicit `--apply`. A dry run reads the namespace, computes every decision, and mutates nothing; the returned `RetentionReport` is identical in shape to an enforced one, with `dry_run=True`.

Each `RetentionItem` carries five things: `entity_id`, `entity_type`, `action`, `reason` (`age` / `unused` / `cascade:<trace_id>`) and `rule` — plus `detail`, a human-readable *why* that names the numbers the decision was made on:

```
DELETE  5   trajectory  reason=age         rule=old-sessions
        why: created 400.0d ago > max_age_days=365
DELETE  4   guideline   reason=cascade:T1  rule=old-sessions
        why: derived from session 5 (metadata.source_task_id == T1), which this rule deletes
FLAG    1   guideline   reason=unused      rule=unused-guidelines
        why: not read for 200.0d > max_unused_days=90 — no metadata.last_accessed on this entity, …
```

Read the dry run before you apply. That is the whole point of it.

## The unused signal

`max_unused_days` measures time since the entity was last *read*, from `metadata.last_accessed`. Nothing in Evolve's core write path sets that key — something has to record the access:

- **`AccessStampPlugin`** (shipped with the [hook seam](memory-hooks.md)) stamps `last_accessed` on every entity returned by a public `search_entities`, via `memory_post_read`. This is the automatic path, and **enabling it is what makes an unused rule mean anything**. Note its cost: fire-and-forget tasks are awaited before the read returns, so every public read pays one metadata write per returned entity (~3.7 ms vs ~0.1 ms for a 10-entity filesystem read).
- **`EvolveClient.record_access(namespace_id, entity_ids)`** is the explicit path, for callers that do not run hooks, or that want to record a *use* that was not a store read — a memory pulled from a cache and actually acted on, say. It goes through the same core function as the plugin (`build_access_stamps`), so the key, the format, and the one-stamp-per-batch behaviour are identical. Running both is harmless.

**If neither is in play, the signal does not exist.** The engine then falls back to `created_at` — and says so, rather than quietly pretending it measured disuse. Every affected item's `detail` names the fallback, and the report carries a run-level warning:

```
warning: 4 of 5 entities carry no metadata.last_accessed, so their disuse was measured from
created_at — for those entities an unused rule behaves like an age rule. Enable
AccessStampPlugin (or call EvolveClient.record_access) for a real recall signal.
```

This is a change from the original prototype, where the fallback was silent and — because nothing called `record_access` — universal.

### When the unused signal is missing

Falling back to `created_at` is only *reporting* the degraded signal — it does not decide whether to act on it. That decision is a per-rule knob, `on_missing_access_signal`, which applies **only to `delete` rules matching on `unused` where the entity has no real `last_accessed` stamp**:

| Value | Behaviour on an unstamped `unused` delete match |
|---|---|
| `skip` | **Default, fail-safe.** Do not act. The entity is recorded in `report.skipped` (and shown in the CLI's "Skipped" table) with the reason, so you can see what was spared. |
| `flag` | Downgrade the delete to a non-destructive `flag`. |
| `delete` | Delete on the `created_at` fallback — the original behaviour, now an explicit opt-in. |

The default is `skip` because a `delete` rule whose signal is silently off (no `AccessStampPlugin`, no `record_access`) would otherwise delete every matched entity from its creation date — data loss with the safety mechanism disabled. `skip` **only bites deletes**: a `flag` rule still flags a never-stamped entity (that is not data loss), an entity *with* a real stamp is unaffected, and an `age`-driven match is unaffected (age from `created_at` is a legitimate signal). Turn a rule up to `delete` once you have confirmed access stamping is on, or to `flag` to build a review queue instead.

The plugin-side skill exposes the same field with the same default, keyed off the recall audit log instead of `metadata.last_accessed`.

## The session cascade

Deleting an old session transcript without deleting the memories extracted from it leaves the data behind under a different name. `cascade_derived: true` on a `delete` rule closes that: when a session entity is deleted, every entity whose `metadata.source_task_id` equals the session's trace id is deleted with it, with `reason="cascade:<trace_id>"`.

Derived entities are deleted **regardless of their own age** — that is the point. A guideline extracted yesterday from a session that ages out today goes with it.

There used to be a convention split here: the MCP server's `save_trajectory` wrote `metadata.task_id` while Phoenix sync wrote `metadata.trace_id`, and the cascade keyed on `trace_id` — so MCP-saved sessions silently never cascaded. Two things now fix that:

1. **`MetadataNormalizerPlugin`** (hook seam) copies `task_id` → `trace_id` on `memory_pre_write`, making `trace_id` canonical for everything written through a backend with hooks enabled.
2. The engine additionally **falls back to `task_id`** when reading a session's trace id, so sessions written *before* the normalizer existed still cascade. `trace_id` wins when both are present.

Both paths are covered by tests in `tests/unit/test_retention.py`.

## Plugin-side retention (evolve-lite)

The `evolve-lite` plugin ships a `retention` skill with the same shape — same rule schema, same flag/delete actions, same dry-run default, same cascade concept — running over the plugin's `.evolve/` file store instead of a backend. It is stdlib-only (`plugin-source/lib/retention.py`), because plugin scripts run in whatever Python the host provides.

```bash
python3 <plugin>/skills/evolve-lite/retention/scripts/run_retention.py           # dry run
python3 <plugin>/skills/evolve-lite/retention/scripts/run_retention.py --apply   # enforce
```

Rules live in a `retention:` block in `evolve.config.yaml`, or in a standalone file passed with `--policy`. Every applied action appends an `event: "retention"` row to `.evolve/audit.log`.

### Where it is weaker than the package side

The plugin store is markdown files with small frontmatter, not a backend with per-entity metadata, so each signal degrades. These gaps are real and worth knowing before you point a `delete` rule at the store:

| Signal | Package | Plugin | Consequence |
|---|---|---|---|
| age | `created_at` on the entity | **file mtime** — there is no `created_at` in the store | mtime is a *modification* time: editing an entity resets its age clock. (Flagging deliberately restores the mtime, so the sweep itself never resets it.) |
| unused | `metadata.last_accessed`, stamped automatically by `AccessStampPlugin` | latest `recall` row in `.evolve/audit.log` naming the entity id `<type>/<name>` | the recall audit is **model-invoked** (the agent runs `audit_recall.py` per `EVOLVE.md`), so a missing row means "no recorded recall", not "not used". A degraded-signal warning is emitted, same as package-side. |
| cascade | `metadata.source_task_id == trace_id`, stamped by the writers | a `trajectory:` frontmatter key naming the session file | **nothing in the shipped plugin writes that key today.** `entity_io` supports and preserves it, but neither the save flow nor `adapt-memory` populates it — so `cascade_derived` is effectively inert plugin-side unless the link was set by hand or by a downstream tool. Deleting a session does *not* clean up its derived memories the way the package side does. |

Trajectory files are opaque JSON with no frontmatter, so a `flag` on a trajectory is recorded in the audit log only, not on the file.

### What is excluded from the plugin sweep

- **`.evolve/entities/subscribed/`** — git clones owned by the `sync` skill. A local delete there would simply be restored by the next sync, so subscribed entities are skipped entirely.
- **`.evolve/public/`** — the publish tree; retention does not touch published entities.
- Symlinks and anything under a `.git` directory.

In scope: private entities under `.evolve/entities/` and session files under `.evolve/trajectories/`.

## Operational notes

- The engine scans up to a fetch limit — `RetentionEngine.FETCH_LIMIT` (100,000) by default, overridable per call via the `scan_limit` argument to `apply()` / `evaluate()` — and holds the entities in memory to build the provenance index. When a scan comes back holding exactly the limit, the report carries a `warnings` entry saying entities beyond it were not evaluated (and so not cascaded); raise `scan_limit` or batch the namespace. Very large namespaces will want batching; that is not implemented.
- One failing entity does not abort the sweep — the failure is logged and recorded in `report.errors`, and the remaining entities are processed. The CLI exits non-zero when `errors` is non-empty.
- Naive `created_at` values are treated as UTC.
- Retention is not a hook: it is a periodic sweep you schedule. There is no automatic expiry on write or read.

## Known limitations

- **No per-namespace or global scheduling.** You run the sweep; nothing runs it for you.
- **No restore.** `delete` is final. Use `flag` first if you want a review stage.
- **`max_unused_days` is only as good as your access stamping** — see above. Without `AccessStampPlugin` or `record_access`, it is an age rule wearing a different name.
- **The plugin-side cascade needs a link nothing writes yet** — see the table above.
