"""Exercise retention CLI commands against real storage and the executor."""

import datetime as dt
import json
import signal

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
    result = runner.invoke(app, ["retention", "policies", "put", "p", "-n", "a"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["retention", "policies", "set-rule", "p", "old", "-n", "a", "--max-age-days", "0", "--action", "delete"])
    assert result.exit_code == 0, result.output
    return client


def invoke(*args):
    result = runner.invoke(app, ["retention", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def create():
    return invoke("schedules", "create", "daily", "-n", "a", "--actor", "alice", "--policy", "p", "--schedule", "* * * * *")


def test_schedule_crud_revision_and_namespace(setup):
    assert create()["revision"] == 1
    assert invoke("schedules", "get", "daily", "-n", "a")["actor_id"] == "alice"
    assert invoke("schedules", "list", "-n", "b")["items"] == []
    missing = runner.invoke(app, ["retention", "schedules", "get", "daily", "-n", "b"])
    assert missing.exit_code == 1 and "not found" in missing.stderr
    duplicate = runner.invoke(
        app, ["retention", "schedules", "create", "daily", "-n", "a", "--actor", "alice", "--policy", "p", "--schedule", "* * * * *"]
    )
    assert duplicate.exit_code == 1 and "conflict" in duplicate.stderr
    updated = invoke("schedules", "update", "daily", "-n", "a", "--actor", "bob", "--suspend", "--revision", "1")
    assert updated["revision"] == 2 and updated["definition"]["spec"]["suspend"]
    stale = runner.invoke(app, ["retention", "schedules", "delete", "daily", "-n", "a", "--revision", "1"])
    assert stale.exit_code == 1
    assert invoke("schedules", "delete", "daily", "-n", "a", "--revision", "2")["deleted"]


def test_schedule_validation_and_required_scope(setup):
    result = runner.invoke(app, ["retention", "schedules", "list"])
    assert result.exit_code == 2
    result = runner.invoke(
        app, ["retention", "schedules", "create", "daily", "-n", "a", "--actor", "alice", "--policy", "p", "--schedule", "invalid"]
    )
    assert result.exit_code == 1 and "schedule" in result.stderr


def test_preview_needs_no_backend(setup, monkeypatch):
    """Timing preview works even when no backend is available."""

    def unavailable():
        raise AssertionError("Preview must not initialize storage")

    monkeypatch.setattr("altk_evolve.cli.cli.get_client", unavailable)
    result = invoke("schedules", "preview", "--schedule", "* * * * *", "--after", "2026-01-01T00:00:00+00:00", "--count", "2")
    assert result["next_runs"] == ["2026-01-01T00:01:00+00:00", "2026-01-01T00:02:00+00:00"]


def test_policy_management(setup):
    assert invoke("policies", "get", "p", "-n", "a")["enabled"]
    assert invoke("policies", "list", "-n", "b")["items"] == []
    invoke("policies", "put", "p", "-n", "a", "--disabled")
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
    handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    result = runner.invoke(app, ["retention", "execute", "--once", "--max-workers", "2"])
    assert result.exit_code == 0, result.output
    assert all(signal.getsignal(s) == handler for s, handler in handlers.items())
    job = invoke("jobs", "list", "-n", "a")["items"][0]
    assert job["status"] == "completed"
    detail = invoke("jobs", "get", job["job_id"], "-n", "a")
    assert detail["run"]["actor_id"] == "alice"
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


def test_executor_rejects_invalid_limits():
    for args in (["--max-workers", "0"], ["--poll-seconds", "0"]):
        result = runner.invoke(app, ["retention", "execute", "--once", *args])
        assert result.exit_code == 2


def test_immediate_run_uses_stored_policy_and_audit(setup):
    for namespace in ("a", "b"):
        setup.update_entities(namespace, [Entity(type="fact", content="scoped memory")], False)
    result = invoke("run", "p", "-n", "a", "--actor", "alice")
    assert result["dry_run"]
    assert ScheduleStore(setup).get_run(namespace_id="a", run_id=result["run_id"])["actor_id"] == "alice"
    assert len(setup.scan_entities("a")) == 1
    applied = invoke("run", "p", "-n", "a", "--actor", "alice", "--apply")
    assert not applied["dry_run"]
    assert setup.scan_entities("a") == []
    assert len(setup.scan_entities("b")) == 1
    failed = runner.invoke(app, ["retention", "run", "missing", "-n", "a", "--actor", "alice"])
    assert failed.exit_code == 1


def test_rule_order_replacement_and_removal(setup):
    invoke("policies", "set-rule", "p", "second", "-n", "a", "--max-unused-days", "30", "--action", "flag")
    result = invoke("policies", "set-rule", "p", "old", "-n", "a", "--max-age-days", "90", "--action", "delete")
    assert [r["name"] for r in result["policy"]["rules"]] == ["old", "second"]
    assert result["policy"]["rules"][0]["max_age_days"] == 90
    result = invoke("policies", "put", "p", "-n", "a", "--disabled")
    assert len(result["policy"]["rules"]) == 2
    result = invoke("policies", "remove-rule", "p", "old", "-n", "a")
    assert [r["name"] for r in result["policy"]["rules"]] == ["second"]
    invalid = runner.invoke(app, ["retention", "policies", "set-rule", "p", "bad", "-n", "a"])
    assert invalid.exit_code == 1


def test_partial_schedule_updates_preserve_and_clear_fields(setup):
    invoke(
        "schedules",
        "create",
        "daily",
        "-n",
        "a",
        "--actor",
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
    result = invoke("schedules", "update", "daily", "-n", "a", "--actor", "alice", "--revision", "1", "--suspend")
    assert result["definition"]["dry_run"] is False
    assert result["definition"]["agent_id"] == "agent-a"
    assert result["definition"]["spec"]["timeZone"] == "America/Los_Angeles"
    result = invoke(
        "schedules",
        "update",
        "daily",
        "-n",
        "a",
        "--actor",
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
