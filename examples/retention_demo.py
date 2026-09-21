#!/usr/bin/env python3
"""Demo: data-retention policies in action (issue #275).

Seeds a throwaway Evolve namespace with a realistic mix of memories — a stale,
never-recalled guideline; a fresh one; an old guideline that was recalled
yesterday; an old session (trajectory) and a memory derived from it — then runs
a retention policy against it. It shows:

  * age- and disuse-based matching, with the *why* behind every decision
  * how ``record_access`` (equivalently ``AccessStampPlugin`` on
    ``memory_post_read``) supplies the ``last_accessed`` signal that makes an
    unused rule mean something, and what the engine reports when it is missing
  * session retention with a provenance cascade: deleting an old trajectory
    also deletes the memories derived from it
    (``metadata.source_task_id == trace_id``), including for MCP-shaped
    sessions that only carry ``task_id``
  * dry run vs ``--apply``

Ages are simulated by backdating ``created_at`` in the on-disk store after
seeding, so the demo needs no real waiting. Run:

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


def _backdate(data_dir: str, namespace: str, ages_days: dict[str, int]) -> None:
    """Rewrite ``created_at`` in the on-disk namespace to fake entity ages."""
    store = pathlib.Path(data_dir) / f"{namespace}.json"
    data = json.loads(store.read_text())
    for entity in data["entities"]:
        for prefix, age in ages_days.items():
            if str(entity["content"]).startswith(prefix):
                entity["created_at"] = _iso(age)
    store.write_text(json.dumps(data))


def _print_report(report) -> None:
    if not report.deleted and not report.flagged:
        print("    (nothing matched)")
    for item in [*report.deleted, *report.flagged, *report.skipped]:
        print(f"    {item.action.upper():<7} {item.entity_id:<3} {item.entity_type:<10} reason={item.reason:<12} rule={item.rule}")
        print(f"            why: {item.detail}")
    for warning in report.warnings:
        print(f"    warning: {warning}")


def _decision_table(engine, namespace, policy) -> None:
    """Show each memory's decision (KEEP / FLAG / DELETE) and why."""
    entities = sorted(engine.client.get_all_entities(namespace), key=lambda e: e.id)
    acted = {it.entity_id: it for it in engine.evaluate(namespace, policy, NOW)}
    print("Decision & why (how the engine derived each outcome):")
    for e in entities:
        src = (e.metadata or {}).get("source_task_id")
        derived = f"  [derived from session {src}]" if src else ""
        if e.id in acted:
            item = acted[e.id]
            decision, why = item.action.upper(), item.detail
        else:
            decision, why = "KEEP", "no rule matched (young enough, and recently read)"
        print(f"    {decision:<6} {e.id} [{e.type}] {str(e.content)[:44]:<44}{derived}")
        print(f"           why: {why}")
    print()


def main() -> int:
    with tempfile.TemporaryDirectory() as data_dir:
        client = EvolveClient(EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=data_dir)))
        client.ensure_namespace("demo")

        # update_entities takes one type per call, so seed by type.
        client.update_entities(
            "demo",
            [
                Entity(content="STALE: deploy only on Fridays", type="guideline"),
                Entity(content="FRESH: prefer uv over pip for installs", type="guideline"),
                Entity(content="USED: the flaky test needs a retry", type="guideline"),
                Entity(content="DERIVED from the old session: always run ruff", type="guideline", metadata={"source_task_id": "T1"}),
            ],
            enable_conflict_resolution=False,
        )
        # An MCP-saved session carries task_id, not trace_id. MetadataNormalizerPlugin
        # copies it across on write; the engine also falls back to it at read time,
        # so the cascade below works for both conventions.
        client.update_entities(
            "demo",
            [Entity(content="SESSION transcript of an old support chat", type="trajectory", metadata={"task_id": "T1"})],
            enable_conflict_resolution=False,
        )

        # Simulate a real store: the stale + used guidelines are 200d old, the
        # session is 400d old, the fresh + derived memories are recent.
        _backdate(data_dir, "demo", ages_days={"STALE": 200, "USED": 200, "SESSION": 400})

        by_content = {str(e.content).split(":")[0]: e.id for e in client.get_all_entities("demo")}

        # This is the whole point of the hook seam for retention: something has
        # to stamp last_accessed. AccessStampPlugin does it automatically on
        # every public read; record_access is the explicit equivalent.
        client.record_access("demo", [by_content["USED"]], when=NOW - datetime.timedelta(days=1))

        policy = RetentionPolicy(
            rules=[
                RetentionRule(name="unused-guidelines", entity_type="guideline", max_unused_days=90, action="flag"),
                RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True),
            ]
        )
        engine = RetentionEngine(client)

        print("Seeded memories:")
        for e in sorted(client.get_all_entities("demo"), key=lambda e: e.id):
            seen = (e.metadata or {}).get("last_accessed")
            print(f"    {e.id} [{e.type}] {e.content}" + (f"   (last read {seen})" if seen else ""))
        print()

        _decision_table(engine, "demo", policy)

        print("DRY RUN (what the policy would do):")
        _print_report(engine.apply("demo", policy, now=NOW, dry_run=True))
        remaining_before = {e.id for e in client.get_all_entities("demo")}
        print(f"    store still has {len(remaining_before)} entities (dry run mutates nothing)\n")

        print("APPLY:")
        _print_report(engine.apply("demo", policy, now=NOW, dry_run=False))
        print()

        print("Store after apply:")
        for e in sorted(client.get_all_entities("demo"), key=lambda e: e.id):
            flag = " <-- FLAGGED" if (e.metadata or {}).get("retention_flagged_at") else ""
            print(f"    {e.id} [{e.type}] {e.content}{flag}")

        print(
            "\nResult: the stale, never-recalled guideline was flagged for review; the\n"
            "guideline recalled yesterday survived the same rule because record_access\n"
            "stamped last_accessed; the 400-day-old session and the memory derived from\n"
            "it were deleted together (provenance cascade, matched via task_id); the\n"
            "fresh guideline was untouched."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
