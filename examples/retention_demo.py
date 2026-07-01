#!/usr/bin/env python3
"""Demo: data-retention policies in action (issue #275).

Seeds a throwaway Evolve namespace with a realistic mix of memories — a stale
guideline, a fresh one, an old session (trajectory) and a memory derived from
it — then runs a retention policy. Shows:

  * unused/age-based flagging (non-destructive marker)
  * session retention with provenance cascade: deleting an old trajectory also
    deletes the memories derived from it (metadata.source_task_id == trace_id)
  * dry-run vs apply

Ages are simulated by backdating created_at in the on-disk store after seeding,
so the demo needs no real waiting. Run:

    uv run python examples/retention_demo.py
"""

from __future__ import annotations

import datetime
import json
import pathlib
import tempfile

from altk_evolve.backend.filesystem import FilesystemSettings
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.retention import RetentionEngine, RetentionPolicy, RetentionRule
from altk_evolve.schema.core import Entity

NOW = datetime.datetime.now(datetime.UTC)


def _iso(days_ago: int) -> str:
    return (NOW - datetime.timedelta(days=days_ago)).isoformat()


def _backdate(data_dir: str, namespace: str, ages_days: dict[str, int], last_access: dict[str, str]) -> None:
    """Rewrite created_at / last_accessed in the on-disk namespace to fake ages."""
    store = pathlib.Path(data_dir) / f"{namespace}.json"
    data = json.loads(store.read_text())
    for entity in data["entities"]:
        for prefix, age in ages_days.items():
            if str(entity["content"]).startswith(prefix):
                entity["created_at"] = _iso(age)
        for prefix, ts in last_access.items():
            if str(entity["content"]).startswith(prefix):
                entity.setdefault("metadata", {})["last_accessed"] = ts
    store.write_text(json.dumps(data))


def _print_report(report) -> None:
    if not report.deleted and not report.flagged:
        print("    (nothing matched)")
    for item in report.deleted:
        print(f"    DELETE  {item.entity_id:<3} {item.entity_type:<10} reason={item.reason:<12} rule={item.rule}")
    for item in report.flagged:
        print(f"    FLAG    {item.entity_id:<3} {item.entity_type:<10} reason={item.reason:<12} rule={item.rule}")


def _age_days(e, now: datetime.datetime) -> int:
    ca = e.created_at if e.created_at.tzinfo else e.created_at.replace(tzinfo=datetime.UTC)
    return int((now - ca).days)


def _unused_days(e, now: datetime.datetime) -> int:
    la = (e.metadata or {}).get("last_accessed")
    when = None
    if la:
        try:
            when = datetime.datetime.fromisoformat(la.replace("Z", "+00:00"))
        except ValueError:
            when = None
    if when is None:
        when = e.created_at
    when = when if when.tzinfo else when.replace(tzinfo=datetime.UTC)
    return int((now - when).days)


def _why_kept(entity, policy, now: datetime.datetime) -> str:
    checks = []
    for rule in policy.rules:
        if rule.entity_type is not None and entity.type != rule.entity_type:
            continue
        if rule.max_age_days is not None:
            checks.append(f"age {_age_days(entity, now)}d < {rule.max_age_days}d")
        if rule.max_unused_days is not None:
            checks.append(f"idle {_unused_days(entity, now)}d < {rule.max_unused_days}d")
    return "; ".join(checks) if checks else "no retention rule applies to this type"


def _decision_table(engine, namespace, policy, now: datetime.datetime) -> None:
    """Show each memory's decision (KEEP/FLAG/DELETE) and *why* it was derived."""
    entities = sorted(engine.client.get_all_entities(namespace), key=lambda e: e.id)
    acted = {it.entity_id: it for it in engine.evaluate(namespace, policy, now)}
    print("Decision & why (how the engine derived each outcome):")
    for e in entities:
        src = (e.metadata or {}).get("source_task_id")
        derived = f"  [derived from session {src}]" if src else ""
        if e.id in acted:
            it = acted[e.id]
            decision = it.action.upper()
            if it.reason.startswith("cascade:"):
                why = f"its source session {it.reason.split(':', 1)[1]} was deleted → provenance cascade"
            elif it.reason == "age":
                why = f"created {_age_days(e, now)}d ago ≥ rule '{it.rule}'"
            elif it.reason == "unused":
                why = f"not accessed in {_unused_days(e, now)}d ≥ rule '{it.rule}'"
            else:
                why = it.reason
        else:
            decision, why = "KEEP", _why_kept(e, policy, now)
        print(f"    {decision:<6} {e.id} [{e.type}] {str(e.content)[:40]:<40}{derived}")
        print(f"           why: {why}")
    print()


def main() -> int:
    with tempfile.TemporaryDirectory() as data_dir:
        client = EvolveClient(EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=data_dir)))
        client.ensure_namespace("demo")

        # update_entities requires a single type per call, so seed by type.
        client.update_entities(
            "demo",
            [
                Entity(content="STALE: deploy only on Fridays", type="guideline"),
                Entity(content="FRESH: prefer uv over pip for installs", type="guideline"),
                Entity(content="DERIVED from old session: always run ruff", type="guideline", metadata={"source_task_id": "T1"}),
            ],
            enable_conflict_resolution=False,
        )
        client.update_entities(
            "demo",
            [Entity(content="SESSION transcript of an old support chat", type="trajectory", metadata={"trace_id": "T1"})],
            enable_conflict_resolution=False,
        )

        # Simulate a real store: the stale guideline is 200d old and untouched;
        # the session is 400d old; the fresh + derived memories are recent.
        _backdate(
            data_dir,
            "demo",
            ages_days={"STALE": 200, "SESSION": 400},
            last_access={"STALE": _iso(200)},
        )

        policy = RetentionPolicy(
            rules=[
                RetentionRule(name="unused-guidelines", entity_type="guideline", max_unused_days=90, action="flag"),
                RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True),
            ]
        )
        engine = RetentionEngine(client)

        print("Seeded memories:")
        for e in sorted(client.get_all_entities("demo"), key=lambda e: e.id):
            print(f"    {e.id} [{e.type}] {e.content}")
        print()

        _decision_table(engine, "demo", policy, datetime.datetime.now(datetime.UTC))

        print("DRY RUN (what the policy would do):")
        _print_report(engine.apply("demo", policy, dry_run=True))
        remaining_before = {e.id for e in client.get_all_entities("demo")}
        print(f"    store still has {len(remaining_before)} entities (dry run mutates nothing)\n")

        print("APPLY:")
        report = engine.apply("demo", policy, dry_run=False)
        _print_report(report)
        print()

        print("Store after apply:")
        for e in sorted(client.get_all_entities("demo"), key=lambda e: e.id):
            flag = " <-- FLAGGED" if (e.metadata or {}).get("retention_flagged_at") else ""
            print(f"    {e.id} [{e.type}] {e.content}{flag}")

        print(
            "\nResult: the stale guideline was flagged for review; the 400-day-old session\n"
            "and the memory derived from it were deleted together (provenance cascade);\n"
            "the fresh guideline was untouched."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
