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


def test_unused_delete_without_access_stamp_skips_by_default_and_reports_it():
    """FIX 2: an unused DELETE on a never-stamped entity must not silently
    destroy it. The fail-safe default (on_missing_access_signal='skip') spares
    it, records it in report.skipped, and still emits the degraded-signal warning."""
    client = FakeClient([_entity("1", created_days_ago=60)])
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=30, action="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=True)

    assert report.deleted == []  # not deleted on the created_at fallback
    assert [i.entity_id for i in report.skipped] == ["1"]
    skip_detail = report.skipped[0].detail
    assert "on_missing_access_signal=skip" in skip_detail
    assert "created_at" in skip_detail and "AccessStampPlugin" in skip_detail
    assert len(report.warnings) == 1
    assert "1 of 1 entities carry no metadata.last_accessed" in report.warnings[0]


def test_unused_delete_without_stamp_deletes_when_opted_in():
    """on_missing_access_signal='delete' restores the explicit opt-in behaviour."""
    client = FakeClient([_entity("1", created_days_ago=60)])
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=30, action="delete", on_missing_access_signal="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=True)

    assert [i.entity_id for i in report.deleted] == ["1"]
    assert report.skipped == []
    detail = report.deleted[0].detail
    assert "created_at" in detail and "AccessStampPlugin" in detail
    assert len(report.warnings) == 1


def test_unused_delete_without_stamp_downgrades_to_flag_when_configured():
    """on_missing_access_signal='flag' turns the destructive action into a flag."""
    client = FakeClient([_entity("1", created_days_ago=60)])
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=30, action="delete", on_missing_access_signal="flag")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert report.deleted == []
    assert [i.entity_id for i in report.flagged] == ["1"]
    assert "downgraded delete->flag" in report.flagged[0].detail
    assert client.store["1"].metadata["retention_reason"] == "unused"


def test_unused_delete_with_real_stamp_deletes_normally_regardless_of_missing_signal_knob():
    """A real last_accessed stamp is unaffected by on_missing_access_signal."""
    stamped = _entity("1", created_days_ago=300, metadata={"last_accessed": (NOW - datetime.timedelta(days=60)).isoformat()})
    client = FakeClient([stamped])
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=30, action="delete")])  # skip default

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert [i.entity_id for i in report.deleted] == ["1"]
    assert report.skipped == []


def test_flag_rule_flags_never_stamped_entity_even_with_default_skip():
    """skip only bites deletes — a flag action is not data loss, so a flag rule
    flags a never-stamped entity as usual under the default."""
    client = FakeClient([_entity("1", created_days_ago=60)])
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=30, action="flag")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert [i.entity_id for i in report.flagged] == ["1"]
    assert report.skipped == []


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


def test_empty_trace_id_does_not_cascade_unrelated_entities():
    """FIX 1: an empty-string trace_id must not bucket every no-provenance
    entity together and cascade-delete them."""
    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata={"trace_id": ""})
    # two unrelated guidelines that happen to carry an empty/absent source link
    a = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": ""})
    b = _entity("g2", type="guideline", created_days_ago=1, metadata={})
    client = FakeClient([trajectory, a, b])
    policy = RetentionPolicy(
        rules=[RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True)]
    )

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    # only the trajectory itself ages out; nothing cascades off the empty trace
    assert {i.entity_id for i in report.deleted} == {"traj"}
    assert "g1" in client.store
    assert "g2" in client.store


def test_cascade_does_not_match_across_types_on_empty_values():
    """An empty source_task_id must never join a cascade bucket even if the
    session's trace id is also falsy."""
    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata={"task_id": ""})
    derived = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": ""})
    client = FakeClient([trajectory, derived])
    policy = RetentionPolicy(
        rules=[RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True)]
    )

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert {i.entity_id for i in report.deleted} == {"traj"}
    assert "g1" in client.store


def test_cascade_matches_int_trace_id_against_str_source_task_id():
    """FIX 1: coercion is consistent — an int trace_id matches a str
    source_task_id of the same non-empty value."""
    trajectory = _entity("traj", type="trajectory", created_days_ago=400, metadata={"trace_id": 1})
    derived = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": "1"})
    client = FakeClient([trajectory, derived])
    policy = RetentionPolicy(
        rules=[RetentionRule(name="old-sessions", entity_type="trajectory", max_age_days=365, action="delete", cascade_derived=True)]
    )

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    assert {i.entity_id for i in report.deleted} == {"traj", "g1"}
    assert next(i for i in report.deleted if i.entity_id == "g1").reason == "cascade:1"


def test_cascade_only_fires_for_trajectory_typed_deletes():
    """A cascade_derived delete of a NON-trajectory entity must not fan out
    along source_task_id. A rule with entity_type=None + cascade_derived=True
    that matches a non-trajectory carrier (here a guideline holding a trace_id)
    would otherwise mass-delete every entity linked by source_task_id."""
    # A non-trajectory entity that happens to carry a trace/task id ...
    carrier = _entity("carrier", type="guideline", created_days_ago=400, metadata={"trace_id": "T1", "task_id": "T1"})
    # ... and unrelated entities linked to that id via source_task_id.
    linked_a = _entity("g1", type="guideline", created_days_ago=1, metadata={"source_task_id": "T1"})
    linked_b = _entity("n1", type="note", created_days_ago=1, metadata={"source_task_id": "T1"})
    client = FakeClient([carrier, linked_a, linked_b])
    # entity_type=None matches any type; cascade_derived is on.
    policy = RetentionPolicy(rules=[RetentionRule(name="broad", entity_type=None, max_age_days=365, action="delete", cascade_derived=True)])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=False)

    # Only the carrier (which aged out) is deleted; nothing cascades because it
    # is not a trajectory.
    assert {i.entity_id for i in report.deleted} == {"carrier"}
    assert "g1" in client.store
    assert "n1" in client.store


def test_scan_limit_boundary_emits_warning():
    """FIX 3: when the fetch returns exactly the limit, warn that entities
    beyond it were not evaluated."""
    client = FakeClient([_entity(str(i), created_days_ago=400) for i in range(3)])
    policy = RetentionPolicy(rules=[RetentionRule(name="old", max_age_days=90, action="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=True, scan_limit=3)

    assert any("fetch limit of 3" in w for w in report.warnings)


def test_scan_limit_under_boundary_emits_no_warning():
    client = FakeClient([_entity(str(i), created_days_ago=400) for i in range(2)])
    policy = RetentionPolicy(rules=[RetentionRule(name="old", max_age_days=90, action="delete")])

    report = RetentionEngine(client).apply("ns", policy, now=NOW, dry_run=True, scan_limit=100)

    assert not any("fetch limit" in w for w in report.warnings)


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
    # opt into deleting on the created_at fallback so the never-stamped "2" is
    # the one this test exercises (the fail-safe default would merely skip it).
    policy = RetentionPolicy(rules=[RetentionRule(name="idle", max_unused_days=90, action="delete", on_missing_access_signal="delete")])

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


def test_real_store_dry_run_mutates_nothing(tmp_path):
    """FIX 5: the primary safety guarantee, against a real filesystem backend.

    Seed an old entity, run apply(dry_run=True), and assert it still exists and
    no retention_* metadata was written to it."""
    from altk_evolve.backend.filesystem import FilesystemSettings
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.schema.core import Entity

    client = EvolveClient(EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))
    client.ensure_namespace("ns")
    client.update_entities("ns", [Entity(content="an old guideline", type="guideline")], enable_conflict_resolution=False)
    entity = client.get_all_entities("ns")[0]
    policy = RetentionPolicy(rules=[RetentionRule(name="old", max_age_days=90, action="delete")])
    # evaluate as-of far in the future so the just-created entity is well past 90d.
    future = entity.created_at + datetime.timedelta(days=500)

    report = RetentionEngine(client).apply("ns", policy, now=future, dry_run=True)

    # dry run would delete it, but must not have
    assert [i.entity_id for i in report.deleted] == [entity.id]
    survivors = client.get_all_entities("ns")
    assert [e.id for e in survivors] == [entity.id]  # still there
    meta = survivors[0].metadata or {}
    assert "retention_flagged_at" not in meta and "retention_reason" not in meta


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
    # DELETE rule ordered before the FLAG rule so it is reachable (first-match).
    assert [r.name for r in policy.rules] == ["unused-guidelines", "stale-guidelines", "old-sessions"]
    unused = policy.rules[0]
    assert unused.action == "delete"
    assert unused.on_missing_access_signal == "skip"  # fail-safe default, explicit in the example
