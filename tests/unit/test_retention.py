"""Tests for the data-retention engine and policy (issue #275).

The engine is exercised against an in-memory fake client so the tests are fast,
deterministic, and backend-agnostic. ``now`` is injected for stable ages.
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
    engine = RetentionEngine(client)
    policy = RetentionPolicy(rules=[RetentionRule(name="stale", max_age_days=90, action="flag")])

    report = engine.apply("ns", policy, now=NOW, dry_run=False)

    assert [i.entity_id for i in report.flagged] == ["1"]
    assert report.deleted == []
    assert client.store["1"].metadata["retention_reason"] == "age"
    assert client.store["1"].metadata["retention_rule"] == "stale"
    assert "retention_flagged_at" in client.store["1"].metadata
    # entity "2" is young; untouched
    assert "retention_flagged_at" not in client.store["2"].metadata


def test_age_delete_removes_old_entities():
    client = FakeClient([_entity("1", created_days_ago=400)])
    engine = RetentionEngine(client)
    policy = RetentionPolicy(rules=[RetentionRule(name="old", max_age_days=365, action="delete")])

    report = engine.apply("ns", policy, now=NOW, dry_run=False)

    assert client.deleted == ["1"]
    assert [i.entity_id for i in report.deleted] == ["1"]


# ── unused-based ──────────────────────────────────────────────────────


def test_unused_uses_last_accessed_when_present():
    recently_used = _entity("1", created_days_ago=300, metadata={"last_accessed": (NOW - datetime.timedelta(days=5)).isoformat()})
    long_idle = _entity("2", created_days_ago=300, metadata={"last_accessed": (NOW - datetime.timedelta(days=60)).isoformat()})
    engine = RetentionEngine(FakeClient([recently_used, long_idle]))
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=30, action="flag")])

    report = engine.apply("ns", policy, now=NOW, dry_run=False)

    assert [i.entity_id for i in report.flagged] == ["2"]
    assert report.flagged[0].reason == "unused"


def test_unused_falls_back_to_created_at_when_never_accessed():
    engine = RetentionEngine(FakeClient([_entity("1", created_days_ago=60)]))
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=30, action="delete")])

    report = engine.apply("ns", policy, now=NOW, dry_run=False)

    assert [i.entity_id for i in report.deleted] == ["1"]


# ── dry run ───────────────────────────────────────────────────────────


def test_dry_run_reports_without_mutating():
    client = FakeClient([_entity("1", created_days_ago=400)])
    engine = RetentionEngine(client)
    policy = RetentionPolicy(rules=[RetentionRule(name="old", max_age_days=90, action="delete")])

    report = engine.apply("ns", policy, now=NOW, dry_run=True)

    assert report.dry_run is True
    assert [i.entity_id for i in report.deleted] == ["1"]  # would delete
    assert client.deleted == []  # but did not
    assert "1" in client.store


# ── session retention + provenance cascade ────────────────────────────


def test_cascade_delete_removes_derived_memories():
    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata={"trace_id": "T1"})
    derived = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": "T1"})
    unrelated = _entity("g2", type="guideline", created_days_ago=1, metadata={"source_task_id": "T2"})
    client = FakeClient([trajectory, derived, unrelated])
    engine = RetentionEngine(client)
    policy = RetentionPolicy(
        rules=[RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True)]
    )

    report = engine.apply("ns", policy, now=NOW, dry_run=False)

    deleted_ids = {i.entity_id for i in report.deleted}
    assert deleted_ids == {"traj", "g1"}  # the young derived guideline goes with its session
    assert "g2" not in deleted_ids  # different trace, untouched
    cascade = next(i for i in report.deleted if i.entity_id == "g1")
    assert cascade.reason == "cascade:T1"


def test_cascade_off_leaves_derived_memories():
    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata={"trace_id": "T1"})
    derived = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": "T1"})
    client = FakeClient([trajectory, derived])
    engine = RetentionEngine(client)
    policy = RetentionPolicy(rules=[RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete")])

    report = engine.apply("ns", policy, now=NOW, dry_run=False)

    assert {i.entity_id for i in report.deleted} == {"traj"}
    assert "g1" in client.store


# ── policy schema ─────────────────────────────────────────────────────


def test_rule_requires_a_threshold():
    with pytest.raises(ValueError, match="max_age_days"):
        RetentionRule(name="bad")


def test_policy_from_mapping_roundtrip():
    policy = RetentionPolicy.from_mapping({"rules": [{"name": "r", "entity_type": "guideline", "max_age_days": 30, "action": "delete"}]})
    assert policy.rules[0].name == "r"
    assert policy.rules[0].action == "delete"


def test_policy_from_file_json(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text('{"rules": [{"name": "r", "max_age_days": 30}]}', encoding="utf-8")
    policy = RetentionPolicy.from_file(p)
    assert policy.rules[0].max_age_days == 30
    assert policy.rules[0].action == "flag"  # default
