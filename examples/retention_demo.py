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
