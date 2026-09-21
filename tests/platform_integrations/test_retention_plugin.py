"""Tests for the evolve-lite plugin retention engine (plugin-source/lib/retention.py).

Imported directly from plugin-source (the source of truth), like test_pii.py.
Runs against a fabricated ``.evolve/`` tree in tmp_path; ``now`` is injected
and file ages are set via ``os.utime`` for stable, deterministic ages.
"""

import datetime
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "plugin-source" / "lib"))
import config  # noqa: E402
import retention  # noqa: E402

pytestmark = [pytest.mark.platform_integrations, pytest.mark.unit]

NOW = datetime.datetime(2026, 6, 29, tzinfo=datetime.timezone.utc)


def _set_age(path, days_old):
    ts = (NOW - datetime.timedelta(days=days_old)).timestamp()
    os.utime(path, (ts, ts))


def _write_entity(evolve_dir, name, type="guideline", days_old=0, trajectory=None):
    type_dir = evolve_dir / "entities" / type
    type_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"type: {type}", "trigger: when testing"]
    if trajectory:
        lines.append(f"trajectory: {trajectory}")
    lines += ["---", "", f"content of {name}", ""]
    path = type_dir / f"{name}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    _set_age(path, days_old)
    return path


def _write_trajectory(evolve_dir, name, days_old=0):
    traj_dir = evolve_dir / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)
    path = traj_dir / name
    path.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}), encoding="utf-8")
    _set_age(path, days_old)
    return path


def _recall(evolve_dir, entity_id, days_ago):
    row = {
        "event": "recall",
        "session_id": "s1",
        "entities": [entity_id],
        "ts": (NOW - datetime.timedelta(days=days_ago)).isoformat(),
    }
    with (evolve_dir / "audit.log").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


@pytest.fixture
def evolve_dir(tmp_path):
    d = tmp_path / ".evolve"
    d.mkdir()
    return d


def _rules(*raw):
    return retention.validate_rules(list(raw))


# ── age-based ─────────────────────────────────────────────────────────


def test_age_flag_marks_old_entities_without_deleting(evolve_dir):
    old = _write_entity(evolve_dir, "old", days_old=100)
    young = _write_entity(evolve_dir, "young", days_old=10)
    rules = _rules({"name": "stale", "max_age_days": 90, "action": "flag"})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert [a["id"] for a in report["flagged"]] == ["guideline/old"]
    assert report["deleted"] == []
    text = old.read_text(encoding="utf-8")
    assert "retention_flagged_at:" in text
    assert "retention_reason: age" in text
    assert "retention_rule: stale" in text
    assert "retention_" not in young.read_text(encoding="utf-8")


def test_age_delete_removes_old_entities(evolve_dir):
    old = _write_entity(evolve_dir, "ancient", days_old=400)
    rules = _rules({"name": "old", "max_age_days": 365, "action": "delete"})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert [a["id"] for a in report["deleted"]] == ["guideline/ancient"]
    assert not old.exists()


# ── unused-based ──────────────────────────────────────────────────────


def test_unused_uses_recall_audit_when_present(evolve_dir):
    _write_entity(evolve_dir, "used", days_old=300)
    _write_entity(evolve_dir, "idle", days_old=300)
    _recall(evolve_dir, "guideline/used", days_ago=5)
    _recall(evolve_dir, "guideline/idle", days_ago=60)
    rules = _rules({"name": "idle", "max_unused_days": 30, "action": "flag"})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert [a["id"] for a in report["flagged"]] == ["guideline/idle"]
    assert report["flagged"][0]["reason"] == "unused"


def test_unused_delete_without_recall_skips_by_default_and_reports_it(evolve_dir):
    """FIX 2: an unused DELETE on a never-recalled entity must not silently
    unlink it. The fail-safe default spares it, reports it as skipped, and still
    emits the degraded-signal warning."""
    never_recalled = _write_entity(evolve_dir, "forgotten", days_old=60)
    rules = _rules({"name": "idle", "max_unused_days": 30, "action": "delete"})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert report["deleted"] == []
    assert never_recalled.exists()  # spared
    assert [a["id"] for a in report["skipped"]] == ["guideline/forgotten"]
    assert "on_missing_access_signal=skip" in report["skipped"][0]["detail"]
    assert len(report["warnings"]) == 1
    assert "no recall row" in report["warnings"][0]


def test_unused_delete_without_recall_deletes_when_opted_in(evolve_dir):
    """on_missing_access_signal='delete' restores the mtime-fallback delete."""
    never_recalled = _write_entity(evolve_dir, "forgotten", days_old=60)
    rules = _rules({"name": "idle", "max_unused_days": 30, "action": "delete", "on_missing_access_signal": "delete"})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert [a["id"] for a in report["deleted"]] == ["guideline/forgotten"]
    assert not never_recalled.exists()
    assert "file mtime" in report["deleted"][0]["detail"]
    assert report["skipped"] == []


def test_unused_delete_without_recall_downgrades_to_flag_when_configured(evolve_dir):
    """on_missing_access_signal='flag' turns the delete into a flag."""
    entity = _write_entity(evolve_dir, "forgotten", days_old=60)
    rules = _rules({"name": "idle", "max_unused_days": 30, "action": "delete", "on_missing_access_signal": "flag"})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert report["deleted"] == []
    assert [a["id"] for a in report["flagged"]] == ["guideline/forgotten"]
    assert "downgraded delete->flag" in report["flagged"][0]["detail"]
    assert "retention_reason: unused" in entity.read_text(encoding="utf-8")


def test_unused_delete_with_recall_deletes_normally_under_default_skip(evolve_dir):
    """A recorded recall (idle past the threshold) is a real signal — the
    default skip does not spare it."""
    entity = _write_entity(evolve_dir, "idle", days_old=300)
    _recall(evolve_dir, "guideline/idle", days_ago=60)
    rules = _rules({"name": "idle", "max_unused_days": 30, "action": "delete"})  # skip default

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert [a["id"] for a in report["deleted"]] == ["guideline/idle"]
    assert not entity.exists()
    assert report["skipped"] == []


def test_flag_rule_flags_never_recalled_entity_under_default_skip(evolve_dir):
    """skip only bites deletes — a flag rule flags a never-recalled entity."""
    entity = _write_entity(evolve_dir, "forgotten", days_old=60)
    rules = _rules({"name": "idle", "max_unused_days": 30, "action": "flag"})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert [a["id"] for a in report["flagged"]] == ["guideline/forgotten"]
    assert report["skipped"] == []
    assert "retention_flagged_at" in entity.read_text(encoding="utf-8")


def test_no_degraded_signal_warning_without_an_unused_rule(evolve_dir):
    _write_entity(evolve_dir, "old", days_old=400)
    rules = _rules({"name": "old", "max_age_days": 365, "action": "flag"})

    report = retention.run(evolve_dir, rules, dry_run=True, now=NOW)

    assert report["warnings"] == []
    assert "file mtime" in report["flagged"][0]["detail"]


# ── dry run ───────────────────────────────────────────────────────────


def test_dry_run_reports_without_mutating(evolve_dir):
    doomed = _write_entity(evolve_dir, "doomed", days_old=400)
    flaggable = _write_entity(evolve_dir, "flaggable", type="note", days_old=200)
    before = flaggable.read_text(encoding="utf-8")
    rules = _rules(
        {"name": "old", "entity_type": "guideline", "max_age_days": 90, "action": "delete"},
        {"name": "stale-notes", "entity_type": "note", "max_age_days": 90, "action": "flag"},
    )

    report = retention.run(evolve_dir, rules, dry_run=True, now=NOW)

    assert report["dry_run"] is True
    assert [a["id"] for a in report["deleted"]] == ["guideline/doomed"]  # would delete
    assert [a["id"] for a in report["flagged"]] == ["note/flaggable"]  # would flag
    assert doomed.exists()  # but did not delete
    assert flaggable.read_text(encoding="utf-8") == before  # and did not flag
    assert not (evolve_dir / "audit.log").exists()  # no audit rows on dry run


# ── session retention + provenance cascade ────────────────────────────


def test_cascade_delete_removes_derived_memories(evolve_dir):
    session = _write_trajectory(evolve_dir, "trajectory_2025-05-01T00-00-00_T1.json", days_old=400)
    derived = _write_entity(evolve_dir, "derived", days_old=1, trajectory=f".evolve/trajectories/{session.name}")
    unrelated = _write_entity(evolve_dir, "unrelated", days_old=1, trajectory=".evolve/trajectories/trajectory_other_T2.json")
    rules = _rules({"name": "old-sessions", "entity_type": "trajectory", "max_age_days": 365, "action": "delete", "cascade_derived": True})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    deleted_ids = {a["id"] for a in report["deleted"]}
    assert deleted_ids == {f"trajectories/{session.name}", "guideline/derived"}
    assert not session.exists()
    assert not derived.exists()
    assert unrelated.exists()  # different session, untouched
    cascade = next(a for a in report["deleted"] if a["id"] == "guideline/derived")
    assert cascade["reason"] == f"cascade:{session.name}"
    assert "trajectory: frontmatter" in cascade["detail"]


def test_cascade_is_inert_without_a_trajectory_frontmatter_link(evolve_dir):
    """Documented gap: nothing in the shipped plugin writes ``trajectory:``.

    A memory saved by the normal flow carries no link, so deleting its session
    does NOT cascade to it — unlike the package side, where the link is
    metadata the writers actually stamp.
    """
    session = _write_trajectory(evolve_dir, "trajectory_2025-05-01T00-00-00_T1.json", days_old=400)
    unlinked = _write_entity(evolve_dir, "saved-normally", days_old=1)
    rules = _rules({"name": "old-sessions", "entity_type": "trajectory", "max_age_days": 365, "action": "delete", "cascade_derived": True})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert {a["id"] for a in report["deleted"]} == {f"trajectories/{session.name}"}
    assert unlinked.exists()


def test_empty_trajectory_link_does_not_cascade(evolve_dir):
    """FIX 1 (plugin): an empty ``trajectory:`` frontmatter value is not a real
    provenance link and must never join a cascade bucket."""
    session = _write_trajectory(evolve_dir, "trajectory_2025-05-01T00-00-00_T1.json", days_old=400)
    # frontmatter carries a literal `trajectory:` line with an empty value
    type_dir = evolve_dir / "entities" / "guideline"
    type_dir.mkdir(parents=True, exist_ok=True)
    empty_link = type_dir / "empty-link.md"
    empty_link.write_text("---\ntype: guideline\ntrajectory: \n---\n\ncontent\n", encoding="utf-8")
    _set_age(empty_link, 1)
    rules = _rules({"name": "old-sessions", "entity_type": "trajectory", "max_age_days": 365, "action": "delete", "cascade_derived": True})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert {a["id"] for a in report["deleted"]} == {f"trajectories/{session.name}"}
    assert empty_link.exists()


def test_cascade_off_leaves_derived_memories(evolve_dir):
    session = _write_trajectory(evolve_dir, "trajectory_2025-05-01T00-00-00_T1.json", days_old=400)
    derived = _write_entity(evolve_dir, "derived", days_old=1, trajectory=f".evolve/trajectories/{session.name}")
    rules = _rules({"name": "old-sessions", "entity_type": "trajectory", "max_age_days": 365, "action": "delete"})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert {a["id"] for a in report["deleted"]} == {f"trajectories/{session.name}"}
    assert derived.exists()


# ── flag mechanics ────────────────────────────────────────────────────


def test_flag_preserves_mtime_so_age_clock_is_stable(evolve_dir):
    old = _write_entity(evolve_dir, "old", days_old=100)
    mtime_before = old.stat().st_mtime
    rules = _rules({"name": "stale", "max_age_days": 90, "action": "flag"})

    retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert old.stat().st_mtime == pytest.approx(mtime_before)
    # Re-running upserts (no duplicate marker lines).
    retention.run(evolve_dir, rules, dry_run=False, now=NOW)
    text = old.read_text(encoding="utf-8")
    assert text.count("retention_flagged_at:") == 1


def test_apply_appends_retention_audit_rows(evolve_dir):
    _write_entity(evolve_dir, "old", days_old=400)
    rules = _rules({"name": "old", "max_age_days": 365, "action": "delete"})

    retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    rows = [json.loads(line) for line in (evolve_dir / "audit.log").read_text(encoding="utf-8").splitlines()]
    assert any(r.get("event") == "retention" and r.get("action") == "delete" and r.get("entity") == "guideline/old" for r in rows)


# ── scope ─────────────────────────────────────────────────────────────


def test_subscribed_entities_are_out_of_scope(evolve_dir):
    sub_dir = evolve_dir / "entities" / "subscribed" / "team-repo" / "guideline"
    sub_dir.mkdir(parents=True)
    sub = sub_dir / "shared.md"
    sub.write_text("---\ntype: guideline\ntrigger: t\n---\n\nshared\n", encoding="utf-8")
    _set_age(sub, 400)
    rules = _rules({"name": "old", "max_age_days": 90, "action": "delete"})

    report = retention.run(evolve_dir, rules, dry_run=False, now=NOW)

    assert report["deleted"] == []
    assert sub.exists()


# ── rule schema ───────────────────────────────────────────────────────


def test_rule_requires_a_threshold():
    with pytest.raises(ValueError, match="max_age_days"):
        retention.validate_rules([{"name": "bad"}])


def test_rule_rejects_unknown_action():
    with pytest.raises(ValueError, match="action"):
        retention.validate_rules([{"name": "bad", "max_age_days": 30, "action": "purge"}])


def test_rule_rejects_unknown_missing_signal_policy():
    with pytest.raises(ValueError, match="on_missing_access_signal"):
        retention.validate_rules([{"name": "bad", "max_unused_days": 30, "action": "delete", "on_missing_access_signal": "nuke"}])


def test_rule_defaults_missing_signal_to_skip():
    rules = retention.validate_rules([{"name": "r", "max_unused_days": 30, "action": "delete"}])
    assert rules[0]["on_missing_access_signal"] == "skip"


def test_load_rules_from_config_yaml_block():
    cfg = config._parse_yaml(
        "retention:\n"
        "  rules:\n"
        "    - name: stale\n"
        "      entity_type: guideline\n"
        "      max_age_days: 90\n"
        "      action: flag\n"
        "    - name: old-sessions\n"
        "      entity_type: trajectory\n"
        "      max_age_days: 365\n"
        "      action: delete\n"
        "      cascade_derived: true\n"
    )
    rules = retention.load_rules(cfg)
    assert [r["name"] for r in rules] == ["stale", "old-sessions"]
    assert rules[0]["action"] == "flag"
    assert rules[1]["cascade_derived"] is True


def test_load_rules_defaults_to_empty():
    assert retention.load_rules({}) == []
    assert retention.load_rules(None) == []
