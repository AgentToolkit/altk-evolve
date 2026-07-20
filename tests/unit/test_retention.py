"""Tests for the data-retention engine and policy (issue #275).

The engine is exercised against an in-memory fake client so the tests are fast,
deterministic and backend-agnostic. ``now`` is injected for stable ages.
"""

import datetime

import pytest

from altk_evolve.retention.engine import RetentionEngine
from altk_evolve.retention.policy import RetentionPolicy, RetentionRule
from altk_evolve.schema.core import RecordedEntity

pytestmark = pytest.mark.unit

NOW = datetime.datetime(2026, 6, 29, tzinfo=datetime.UTC)


def _entity(id, type="guideline", content="x", created_days_ago=0, metadata=None):
    return RecordedEntity(
        id=id,
        type=type,
        content=content,
        metadata=metadata or {},
        created_at=NOW - datetime.timedelta(days=created_days_ago),
    )


class FakeClient:
    """Minimal stand-in implementing only what RetentionEngine calls."""

    def __init__(self, entities):
        self.store = {e.id: e for e in entities}
        self.deleted: list[str] = []

    def get_all_entities(self, namespace_id, filters=None, limit=100):
        return list(self.store.values())

    def delete_entity_by_id(self, namespace_id, entity_id):
        self.deleted.append(entity_id)
        self.store.pop(entity_id, None)

    def patch_entity_metadata(self, namespace_id, entity_id, metadata_updates):
        e = self.store[entity_id]
        e.metadata = {**(e.metadata or {}), **metadata_updates}
        return e


# ── age-based ─────────────────────────────────────────────────────────


def test_age_flag_marks_old_entities_without_deleting():
    client = FakeClient([_entity("1", created_days_ago=100), _entity("2", created_days_ago=10)])
    policy = RetentionPolicy(rules=[RetentionRule(name="stale", max_age_days=90, action="flag")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert [i.entity_id for i in report.flagged] == ["1"]
    assert report.deleted == []
    assert client.store["1"].metadata["retention_reason"] == "age"
    assert client.store["1"].metadata["retention_rule"] == "stale"
    assert "retention_flagged_at" in client.store["1"].metadata
    # entity "2" is young; untouched
    assert "retention_flagged_at" not in client.store["2"].metadata


def test_age_delete_removes_old_entities():
    client = FakeClient([_entity("1", created_days_ago=400)])
    policy = RetentionPolicy(rules=[RetentionRule(name="old", max_age_days=365, action="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert client.deleted == ["1"]
    assert [i.entity_id for i in report.deleted] == ["1"]
    assert "max_age_days=365" in report.deleted[0].detail


def test_first_matching_rule_wins():
    client = FakeClient([_entity("1", type="guideline", created_days_ago=400)])
    policy = RetentionPolicy(
        rules=[
            RetentionRule(name="flag-first", max_age_days=90, action="flag"),
            RetentionRule(name="delete-later", max_age_days=365, action="delete"),
        ]
    )

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert [i.rule for i in report.flagged] == ["flag-first"]
    assert report.deleted == []


# ── unused-based ──────────────────────────────────────────────────────


def test_unused_uses_last_accessed_when_present():
    recently_used = _entity("1", created_days_ago=300, metadata={"last_accessed": (NOW - datetime.timedelta(days=5)).isoformat()})
    long_idle = _entity("2", created_days_ago=300, metadata={"last_accessed": (NOW - datetime.timedelta(days=60)).isoformat()})
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=30, action="flag")])

    report = RetentionEngine(FakeClient([recently_used, long_idle])).apply("ns", policy, now=NOW, dry_run=False)

    assert [i.entity_id for i in report.flagged] == ["2"]
    assert report.flagged[0].reason == "unused"
    assert "metadata.last_accessed" in report.flagged[0].detail
    # every entity carried a stamp, so no degraded-signal warning
    assert report.warnings == []


def test_unused_without_access_stamp_falls_back_to_created_at_and_says_so():
    """The PoC silently used created_at here. Now the fallback is reported."""
    client = FakeClient([_entity("1", created_days_ago=60)])
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=30, action="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=True)

    assert [i.entity_id for i in report.deleted] == ["1"]
    detail = report.deleted[0].detail
    assert "created_at" in detail and "AccessStampPlugin" in detail
    assert len(report.warnings) == 1
    assert "1 of 1 entities carry no metadata.last_accessed" in report.warnings[0]


def test_no_degraded_signal_warning_without_an_unused_rule():
    client = FakeClient([_entity("1", created_days_ago=400)])
    policy = RetentionPolicy(rules=[RetentionRule(name="old", max_age_days=365, action="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=True)

    assert report.warnings == []


def test_malformed_last_accessed_is_treated_as_missing():
    client = FakeClient([_entity("1", created_days_ago=60, metadata={"last_accessed": "not-a-date"})])
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=30, action="flag")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=True)

    assert [i.entity_id for i in report.flagged] == ["1"]
    assert "created_at" in report.flagged[0].detail


# ── dry run ───────────────────────────────────────────────────────────


def test_dry_run_reports_without_mutating():
    client = FakeClient([_entity("1", created_days_ago=400), _entity("2", type="note", created_days_ago=400)])
    policy = RetentionPolicy(
        rules=[
            RetentionRule(name="old", entity_type="guideline", max_age_days=90, action="delete"),
            RetentionRule(name="stale-notes", entity_type="note", max_age_days=90, action="flag"),
        ]
    )

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=True)

    assert report.dry_run is True
    assert [i.entity_id for i in report.deleted] == ["1"]  # would delete
    assert [i.entity_id for i in report.flagged] == ["2"]  # would flag
    assert client.deleted == []  # but did not
    assert "1" in client.store
    assert "retention_flagged_at" not in client.store["2"].metadata


def test_apply_defaults_to_dry_run():
    client = FakeClient([_entity("1", created_days_ago=400)])
    policy = RetentionPolicy(rules=[RetentionRule(name="old", max_age_days=90, action="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW)

    assert report.dry_run is True
    assert client.deleted == []


def test_backend_failure_is_recorded_and_sweep_continues():
    class Exploding(FakeClient):
        def delete_entity_by_id(self, namespace_id, entity_id):
            if entity_id == "1":
                raise RuntimeError("legal hold")
            super().delete_entity_by_id(namespace_id, entity_id)

    client = Exploding([_entity("1", created_days_ago=400), _entity("2", created_days_ago=400)])
    policy = RetentionPolicy(rules=[RetentionRule(name="old", max_age_days=90, action="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert client.deleted == ["2"]
    assert [i.entity_id for i in report.deleted] == ["2"]
    assert report.errors == ["delete 1: legal hold"]


# ── session retention + provenance cascade ────────────────────────────


def test_cascade_delete_removes_derived_memories():
    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata={"trace_id": "T1"})
    derived = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": "T1"})
    unrelated = _entity("g2", type="guideline", created_days_ago=1, metadata={"source_task_id": "T2"})
    client = FakeClient([trajectory, derived, unrelated])
    policy = RetentionPolicy(
        rules=[RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True)]
    )

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    deleted_ids = {i.entity_id for i in report.deleted}
    assert deleted_ids == {"traj", "g1"}  # the young derived guideline goes with its session
    assert "g2" not in deleted_ids  # different trace, untouched
    cascade = next(i for i in report.deleted if i.entity_id == "g1")
    assert cascade.reason == "cascade:T1"
    assert "derived from session traj" in cascade.detail


def test_cascade_works_for_mcp_shaped_sessions_normalized_by_the_hook_seam():
    """MetadataNormalizerPlugin copies MCP's ``task_id`` into ``trace_id``.

    An MCP-saved trajectory written through the seam therefore carries BOTH
    keys, and the cascade keys on ``trace_id`` exactly as for Phoenix sync.
    """
    from altk_evolve.hooks.plugins.normalizer import normalize_entities

    raw = [{"content": "session", "type": "trajectory", "metadata": {"task_id": "T9"}}]
    normalized = normalize_entities(raw, stamp_created_at=False)
    assert normalized is not None
    assert normalized[0]["metadata"]["trace_id"] == "T9"

    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata=normalized[0]["metadata"])
    derived = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": "T9"})
    client = FakeClient([trajectory, derived])
    policy = RetentionPolicy(
        rules=[RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True)]
    )

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert {i.entity_id for i in report.deleted} == {"traj", "g1"}


def test_cascade_falls_back_to_task_id_for_pre_normalizer_sessions():
    """Sessions written before the normalizer carry only ``task_id``."""
    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata={"task_id": "T5"})
    derived = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": "T5"})
    client = FakeClient([trajectory, derived])
    policy = RetentionPolicy(
        rules=[RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True)]
    )

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert {i.entity_id for i in report.deleted} == {"traj", "g1"}
    assert next(i for i in report.deleted if i.entity_id == "g1").reason == "cascade:T5"


def test_trace_id_wins_over_task_id_when_both_present():
    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata={"trace_id": "T1", "task_id": "T1"})
    derived = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": "T1"})
    client = FakeClient([trajectory, derived])
    policy = RetentionPolicy(
        rules=[RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True)]
    )

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert next(i for i in report.deleted if i.entity_id == "g1").reason == "cascade:T1"


def test_cascade_off_leaves_derived_memories():
    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata={"trace_id": "T1"})
    derived = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": "T1"})
    client = FakeClient([trajectory, derived])
    policy = RetentionPolicy(rules=[RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert {i.entity_id for i in report.deleted} == {"traj"}
    assert "g1" in client.store


def test_cascade_delete_supersedes_a_flag_from_an_earlier_rule():
    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata={"trace_id": "T1"})
    derived = _entity("g1", type="guideline", created_days_ago=200, metadata={"source_task_id": "T1"})
    client = FakeClient([trajectory, derived])
    policy = RetentionPolicy(
        rules=[
            RetentionRule(name="stale-guidelines", entity_type="guideline", max_age_days=90, action="flag"),
            RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True),
        ]
    )

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert {i.entity_id for i in report.deleted} == {"traj", "g1"}
    assert report.flagged == []


# ── record_access: the explicit half of the access signal ─────────────


def test_record_access_stamps_the_same_key_and_format_as_the_plugin(tmp_path):
    """``record_access`` and ``AccessStampPlugin`` must not diverge.

    Both go through ``build_access_stamps``, so one batch shares one ISO-8601
    UTC ``last_accessed`` stamp under the same metadata key.
    """
    from altk_evolve.backend.filesystem import FilesystemSettings
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.hooks.plugins.access_stamp import build_access_stamps
    from altk_evolve.schema.core import Entity

    client = EvolveClient(EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))
    client.ensure_namespace("ns")
    client.update_entities("ns", [Entity(content="a guideline", type="guideline")], enable_conflict_resolution=False)
    entity_id = client.get_all_entities("ns")[0].id

    client.record_access("ns", [entity_id], when=NOW)

    stamped = client.get_all_entities("ns")[0]
    assert stamped.metadata["last_accessed"] == NOW.isoformat()
    # identical to what the plugin core would have produced for the same read
    assert build_access_stamps([{"id": entity_id}], now=lambda: NOW) == [(entity_id, {"last_accessed": NOW.isoformat()})]


def test_record_access_makes_the_unused_rule_meaningful():
    """A recently recorded access rescues an old entity from an unused rule."""
    old_but_used = _entity("1", created_days_ago=300)
    old_and_idle = _entity("2", created_days_ago=300)
    client = FakeClient([old_but_used, old_and_idle])
    # Simulate what record_access / AccessStampPlugin write.
    client.patch_entity_metadata("ns", "1", {"last_accessed": (NOW - datetime.timedelta(days=1)).isoformat()})
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=90, action="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert [i.entity_id for i in report.deleted] == ["2"]
    assert "1" in client.store


def test_record_access_skips_failures_without_raising(tmp_path):
    from altk_evolve.backend.filesystem import FilesystemSettings
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    client = EvolveClient(EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))
    client.ensure_namespace("ns")

    client.record_access("ns", ["does-not-exist"])  # must not raise


# ── policy schema ─────────────────────────────────────────────────────


def test_rule_requires_a_threshold():
    with pytest.raises(ValueError, match="max_age_days"):
        RetentionRule(name="bad")


def test_rule_rejects_unknown_action():
    with pytest.raises(ValueError):
        RetentionRule(name="bad", max_age_days=30, action="purge")


def test_policy_from_mapping_roundtrip():
    policy = RetentionPolicy.from_mapping({"rules": [{"name": "r", "entity_type": "guideline", "max_age_days": 30, "action": "delete"}]})
    assert policy.rules[0].name == "r"
    assert policy.rules[0].action == "delete"


def test_policy_from_mapping_accepts_none():
    assert RetentionPolicy.from_mapping(None).rules == []


def test_policy_from_file_json(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text('{"rules": [{"name": "r", "max_age_days": 30}]}', encoding="utf-8")
    policy = RetentionPolicy.from_file(p)
    assert policy.rules[0].max_age_days == 30
    assert policy.rules[0].action == "flag"  # default


def test_policy_from_file_yaml(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("rules:\n  - name: r\n    entity_type: trajectory\n    max_age_days: 365\n    action: delete\n", encoding="utf-8")
    policy = RetentionPolicy.from_file(p)
    assert policy.rules[0].entity_type == "trajectory"
    assert policy.rules[0].action == "delete"


def test_shipped_example_policy_is_valid():
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / "examples" / "retention.example.yaml"
    policy = RetentionPolicy.from_file(example)
    assert [r.name for r in policy.rules] == ["stale-guidelines", "unused-guidelines", "old-sessions"]
