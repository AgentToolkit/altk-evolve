"""Exercise retention CLI commands against real storage and the executor."""

import datetime as dt
import json
import time

import pytest
from typer.testing import CliRunner

from altk_evolve.cli.cli import app
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.retention.schedule_store import ScheduleStore
from altk_evolve.schema.core import Entity

pytestmark = pytest.mark.unit
runner = CliRunner()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLVE_RETENTION_STORE_PATH", raising=False)
    client = EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path / "data"))))
    monkeypatch.setattr("altk_evolve.cli.cli.get_client", lambda: client)
    for namespace in ("a", "b"):
        client.ensure_namespace(namespace)
    result = runner.invoke(app, ["retention", "policies", "create", "p", "-n", "a"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["retention", "policies", "rules", "add", "p", "--name", "old", "-n", "a", "--max-age-days", "0", "--action", "delete"]
    )
    assert result.exit_code == 0, result.output
    return client


def invoke(*args):
    result = runner.invoke(app, ["retention", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def create():
    return invoke("schedules", "create", "daily", "-n", "a", "--initiated-by", "alice", "--policy", "p", "--schedule", "* * * * *")


def test_schedule_crud_revision_and_namespace(setup):
    assert create()["revision"] == 1
    assert invoke("schedules", "show", "daily", "-n", "a")["initiated_by"] == "alice"
    assert invoke("schedules", "list", "-n", "b")["items"] == []
    missing = runner.invoke(app, ["retention", "schedules", "show", "daily", "-n", "b"])
    assert missing.exit_code == 1 and "not found" in missing.stderr
    duplicate = runner.invoke(
        app, ["retention", "schedules", "create", "daily", "-n", "a", "--initiated-by", "alice", "--policy", "p", "--schedule", "* * * * *"]
    )
    assert duplicate.exit_code == 1 and "conflict" in duplicate.stderr
    updated = invoke("schedules", "update", "daily", "-n", "a", "--initiated-by", "bob", "--suspend", "--revision", "1")
    assert updated["revision"] == 2 and updated["definition"]["spec"]["suspend"]
    stale = runner.invoke(app, ["retention", "schedules", "delete", "daily", "-n", "a", "--revision", "1"])
    assert stale.exit_code == 1
    assert invoke("schedules", "delete", "daily", "-n", "a", "--revision", "2")["deleted"]


def test_schedule_validation_and_required_scope(setup):
    result = runner.invoke(app, ["retention", "schedules", "list"])
    assert result.exit_code == 2
    result = runner.invoke(
        app, ["retention", "schedules", "create", "daily", "-n", "a", "--initiated-by", "alice", "--policy", "p", "--schedule", "invalid"]
    )
    assert result.exit_code == 1 and "schedule" in result.stderr


def test_schedule_show_includes_upcoming_times_without_mutating(setup):
    from zoneinfo import ZoneInfo

    invoke(
        "schedules",
        "create",
        "daily",
        "-n",
        "a",
        "--initiated-by",
        "alice",
        "--policy",
        "p",
        "--schedule",
        "0 2 * * *",
        "--time-zone",
        "America/Los_Angeles",
    )
    catalog = ScheduleStore(setup)
    before = catalog.get("a", "daily")
    result = invoke("schedules", "show", "daily", "-n", "a")
    times = [dt.datetime.fromisoformat(value) for value in result["next_runs"]]
    assert len(times) == 5 and times == sorted(set(times))
    assert all(time > dt.datetime.now(dt.UTC) for time in times)
    assert all(time.astimezone(ZoneInfo("America/Los_Angeles")).hour == 2 for time in times)
    assert result["definition"] == before["definition"]
    assert catalog.get("a", "daily") == before
    assert catalog.jobs("a") == []
    invoke("schedules", "update", "daily", "-n", "a", "--initiated-by", "alice", "--revision", "1", "--suspend")
    assert invoke("schedules", "show", "daily", "-n", "a")["next_runs"] == []


def test_schedule_help_has_show_and_no_preview():
    result = runner.invoke(app, ["retention", "schedules", "--help"])
    assert result.exit_code == 0
    assert "show" in result.stdout and "preview" not in result.stdout


def test_policy_management(setup):
    assert invoke("policies", "show", "p", "-n", "a")["enabled"]
    assert invoke("policies", "list", "-n", "b")["items"] == []
    invoke("policies", "update", "p", "-n", "a", "--disabled")
    assert not invoke("policies", "list", "-n", "a")["items"][0]["enabled"]


@pytest.mark.e2e
def test_cli_executor_and_job_history(setup):
    client = setup
    client.update_entities("a", [Entity(type="fact", content="dry run keeps this")], False)
    create()
    catalog = ScheduleStore(client)
    # Backdate the schedule to make an occurrence due without sleeping.
    with catalog.transaction() as conn:
        catalog.sql(
            conn,
            "UPDATE evolve_retention_schedules SET last_schedule_at=?",
            ((dt.datetime.now(dt.UTC) - dt.timedelta(minutes=2)).isoformat(),),
        )
    from altk_evolve.retention.scheduler import retention_runtime

    client.config.retention_poll_seconds = 0.01
    with retention_runtime(client):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            jobs = catalog.jobs("a")
            if jobs and jobs[0]["status"] == "completed":
                break
            time.sleep(0.01)
    job = invoke("jobs", "list", "-n", "a")["items"][0]
    assert job["status"] == "completed"
    detail = invoke("jobs", "show", job["job_id"], "-n", "a")
    assert detail["run"]["initiated_by"] == "alice"
    assert detail["run"]["report"]["dry_run"]
    assert len(client.scan_entities("a")) == 1
    assert invoke("jobs", "list", "-n", "b")["items"] == []


def test_job_cancel_and_explicit_recovery(setup):
    client = setup
    create()
    catalog = ScheduleStore(client)
    due = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1)
    job = catalog.dispatch("a", "daily", due)
    assert invoke("jobs", "cancel", job, "-n", "a")["cancellation_requested"]
    job = catalog.dispatch("a", "daily", due + dt.timedelta(minutes=1))
    assert catalog.claim("a", job, "dead-worker", due)
    result = runner.invoke(app, ["retention", "jobs", "recover", job, "-n", "a"])
    assert result.exit_code == 1 and "Confirm" in result.stderr
    assert invoke("jobs", "recover", job, "-n", "a", "--worker-stopped")["acknowledged"]


def test_cli_command_tree_matches_resource_actions():
    from typer.main import get_command

    for path, expected, absent in [
        ([], ["policies", "schedules", "jobs", "run", "mark", "sweep", "candidates", "audit"], ["execute", "worker"]),
        (["policies"], ["create", "update", "delete", "show", "list", "rules"], ["put", "set-rule"]),
        (["policies", "rules"], ["add", "list", "update", "remove"], ["set-rule"]),
        (["schedules"], ["create", "show", "list", "update", "delete", "start", "stop"], ["preview", "run"]),
        (["jobs"], ["show", "list", "cancel", "recover"], ["get"]),
    ]:
        result = runner.invoke(app, ["retention", *path, "--help"])
        assert result.exit_code == 0
        group = get_command(app)
        for component in ["retention", *path]:
            group = group.commands[component]
        assert set(group.commands) == set(expected)


def test_immediate_run_uses_stored_policy_and_audit(setup):
    for namespace in ("a", "b"):
        setup.update_entities(namespace, [Entity(type="fact", content="scoped memory")], False)
    result = invoke("run", "p", "-n", "a", "--initiated-by", "alice")
    assert result["dry_run"]
    assert ScheduleStore(setup).get_run(namespace_id="a", run_id=result["run_id"])["initiated_by"] == "alice"
    assert len(setup.scan_entities("a")) == 1
    applied = invoke("run", "p", "-n", "a", "--initiated-by", "alice", "--apply")
    assert not applied["dry_run"]
    assert setup.scan_entities("a") == []
    assert len(setup.scan_entities("b")) == 1
    failed = runner.invoke(app, ["retention", "run", "missing", "-n", "a", "--initiated-by", "alice"])
    assert failed.exit_code == 1


def test_rule_order_replacement_and_removal(setup):
    invoke("policies", "rules", "add", "p", "--name", "second", "-n", "a", "--max-unused-days", "30", "--action", "flag")
    result = invoke("policies", "rules", "update", "p", "--name", "old", "-n", "a", "--max-age-days", "90", "--action", "delete")
    assert [r["name"] for r in result["policy"]["rules"]] == ["old", "second"]
    assert result["policy"]["rules"][0]["max_age_days"] == 90
    result = invoke("policies", "update", "p", "-n", "a", "--disabled")
    assert len(result["policy"]["rules"]) == 2
    result = invoke("policies", "rules", "remove", "p", "--name", "old", "-n", "a")
    assert [r["name"] for r in result["policy"]["rules"]] == ["second"]
    invalid = runner.invoke(app, ["retention", "policies", "rules", "add", "p", "--name", "bad", "-n", "a"])
    assert invalid.exit_code == 1


def test_partial_schedule_updates_preserve_and_clear_fields(setup):
    invoke(
        "schedules",
        "create",
        "daily",
        "-n",
        "a",
        "--initiated-by",
        "alice",
        "--policy",
        "p",
        "--schedule",
        "0 2 * * *",
        "--time-zone",
        "America/Los_Angeles",
        "--agent",
        "agent-a",
        "--starting-deadline-seconds",
        "60",
        "--apply",
    )
    result = invoke("schedules", "update", "daily", "-n", "a", "--initiated-by", "alice", "--revision", "1", "--suspend")
    assert result["definition"]["dry_run"] is False
    assert result["definition"]["agent_id"] == "agent-a"
    assert result["definition"]["spec"]["timeZone"] == "America/Los_Angeles"
    result = invoke(
        "schedules",
        "update",
        "daily",
        "-n",
        "a",
        "--initiated-by",
        "alice",
        "--revision",
        "2",
        "--resume",
        "--dry-run",
        "--clear-agent",
        "--clear-deadline",
    )
    assert result["definition"]["dry_run"] is True
    assert result["definition"]["agent_id"] is None
    assert result["definition"]["spec"]["suspend"] is False
    assert result["definition"]["spec"]["startingDeadlineSeconds"] is None


def test_policy_create_update_delete_and_references(setup):
    duplicate = runner.invoke(app, ["retention", "policies", "create", "p", "-n", "a"])
    assert duplicate.exit_code == 1 and "already exists" in duplicate.stderr
    missing = runner.invoke(app, ["retention", "policies", "update", "missing", "-n", "a"])
    assert missing.exit_code == 1
    create()
    referenced = runner.invoke(app, ["retention", "policies", "delete", "p", "-n", "a"])
    assert referenced.exit_code == 1 and "referenced" in referenced.stderr
    invoke("schedules", "delete", "daily", "-n", "a", "--revision", "1")
    assert invoke("policies", "delete", "p", "-n", "a")["deleted"]


def test_schedule_start_stop_preserves_existing_jobs(setup):
    create()
    store = ScheduleStore(setup)
    due = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1)
    job = store.dispatch("a", "daily", due)
    stopped = invoke("schedules", "stop", "daily", "-n", "a", "--initiated-by", "alice", "--revision", "1")
    assert stopped["definition"]["spec"]["suspend"]
    assert store.get_job("a", job)["status"] == "queued"
    assert store.dispatch("a", "daily", due + dt.timedelta(minutes=1)) is None
    started = invoke("schedules", "start", "daily", "-n", "a", "--initiated-by", "alice", "--revision", "2")
    assert not started["definition"]["spec"]["suspend"]
    assert started["definition"]["policy_id"] == "p"
    stale = runner.invoke(app, ["retention", "schedules", "stop", "daily", "-n", "a", "--initiated-by", "alice", "--revision", "2"])
    assert stale.exit_code == 1


def test_nested_rules_fail_closed_on_missing_or_duplicate(setup):
    duplicate = runner.invoke(app, ["retention", "policies", "rules", "add", "p", "--name", "old", "-n", "a", "--max-age-days", "30"])
    assert duplicate.exit_code == 1
    missing = runner.invoke(app, ["retention", "policies", "rules", "update", "p", "--name", "missing", "-n", "a", "--max-age-days", "30"])
    assert missing.exit_code == 1
    updated = invoke("policies", "rules", "update", "p", "--name", "old", "-n", "a", "--max-age-days", "30")
    assert updated["policy"]["rules"][0]["action"] == "delete"
    assert invoke("policies", "rules", "list", "p", "-n", "a")["items"][0]["max_age_days"] == 30
    assert invoke("policies", "rules", "list", "p", "-n", "a")["items"][0]["name"] == "old"


def test_source_deletion_grace_can_be_configured(setup):
    added = invoke(
        "policies",
        "rules",
        "add",
        "p",
        "-n",
        "a",
        "--name",
        "orphan",
        "--source-deleted",
        "--min-source-deleted-days",
        "7",
        "--action",
        "delete",
    )
    assert added["policy"]["rules"][-1]["min_source_deleted_days"] == 7
    updated = invoke("policies", "rules", "update", "p", "-n", "a", "--name", "orphan", "--min-source-deleted-days", "14")
    assert updated["policy"]["rules"][-1]["min_source_deleted_days"] == 14


def test_source_deletion_grace_can_be_cleared_without_replacing_rule(setup):
    invoke("policies", "rules", "update", "p", "-n", "a", "--name", "old", "--source-deleted", "--min-source-deleted-days", "7")
    conflict = runner.invoke(
        app,
        [
            "retention",
            "policies",
            "rules",
            "update",
            "p",
            "-n",
            "a",
            "--name",
            "old",
            "--clear-source-deleted-days",
            "--min-source-deleted-days",
            "0",
        ],
    )
    assert conflict.exit_code == 1
    assert "Cannot set and clear min_source_deleted_days" in conflict.output
    assert setup.retention("a").list_rules("p")["items"][0]["min_source_deleted_days"] == 7
    updated = invoke("policies", "rules", "update", "p", "-n", "a", "--name", "old", "--clear-source-deleted-days", "--no-source-deleted")
    rule = updated["policy"]["rules"][0]
    assert rule["min_source_deleted_days"] is None
    assert rule["source_deleted"] is False
    assert rule["max_age_days"] == 0
