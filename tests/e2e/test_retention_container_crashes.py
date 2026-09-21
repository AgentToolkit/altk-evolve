"""Opt-in SIGKILL tests with real scheduler containers and disposable PostgreSQL.

Set EVOLVE_TEST_CONTAINER_IMAGE to an image with Evolve's dependencies and an
entrypoint that runs Python (for example uv run --no-sync --project /app python).
The checkout is mounted read-only so the tests exercise the current source.
Run: uv run pytest -v -s -m e2e tests/e2e/test_retention_container_crashes.py
"""

import datetime as dt
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
import uuid


import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session", autouse=True)
def phoenix_server():
    """These database-only tests do not require Phoenix."""
    yield


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


def eventually(predicate, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    raise AssertionError("Timed out waiting for container/database condition")


@pytest.fixture
def containers():
    image = os.environ.get("EVOLVE_TEST_CONTAINER_IMAGE")
    if not image:
        pytest.skip("Set EVOLVE_TEST_CONTAINER_IMAGE to opt into Docker SIGKILL tests")
    import psycopg
    from psycopg.rows import dict_row

    name = "evolve-gc-" + uuid.uuid4().hex[:10]
    names = []
    docker("network", "create", name)
    try:
        database = name + "-db"
        names.append(database)
        docker(
            "run",
            "-d",
            "--name",
            database,
            "--network",
            name,
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "-p",
            "127.0.0.1::5432",
            os.environ.get("EVOLVE_TEST_POSTGRES_IMAGE", "pgvector/pgvector:pg16"),
        )
        eventually(
            lambda: (
                "PostgreSQL init process complete; ready for start up." in docker("logs", database)
                and "listening on IPv4" in docker("logs", database)
            ),
            timeout=30,
        )
        port = docker("port", database, "5432/tcp").rsplit(":", 1)[1]

        def connect():
            try:
                return psycopg.connect(
                    f"postgresql://postgres@127.0.0.1:{port}/postgres", autocommit=True, row_factory=dict_row, connect_timeout=2
                )
            except psycopg.OperationalError:
                return None

        connections = []

        def ready():
            connection = connect()
            if connection is not None:
                connections.append(connection)
                return True
            return False

        eventually(ready, timeout=30)
        conn = connections[0]

        def start(suffix, phase=""):
            container = name + "-" + suffix
            names.append(container)
            docker(
                "run",
                "-d",
                "--name",
                container,
                "--network",
                name,
                "-e",
                f"TEST_DSN=postgresql://postgres@{database}/postgres",
                "-e",
                f"TEST_PAUSE={phase}",
                "-e",
                "PYTHONPATH=/evolve",
                "-e",
                "PYTHONUNBUFFERED=1",
                "-v",
                f"{Path(__file__).resolve().parents[2]}:/evolve:ro",
                image,
                "/evolve/tests/e2e/retention_container_executor.py",
            )
            return container

        with conn:
            yield conn, start
    finally:
        for container in reversed(names):
            try:
                print(container, docker("logs", "--tail", "20", container))
                docker("rm", "-f", "-v", container)
            except subprocess.CalledProcessError as exc:
                print(exc.output)
        docker("network", "rm", name)


@pytest.mark.parametrize("phase", ["mark", "delete", "committed", "heartbeat"])
def test_scheduler_container_sigkill(containers, phase):
    from psycopg import sql
    from altk_evolve.retention.collection import Collection
    from altk_evolve.retention.schedule import CronJobSpec, ScheduleDefinition

    conn, start = containers
    for ns in ("a", "b"):
        table = sql.Identifier("ns_" + ns)
        conn.execute(
            sql.SQL("CREATE TABLE {} (id BIGSERIAL PRIMARY KEY,type TEXT,content TEXT,created_at BIGINT,metadata JSONB)").format(table)
        )
        conn.execute(
            sql.SQL(
                "INSERT INTO {} (type,content,created_at,metadata) SELECT 'fact','literal private memory',0,jsonb_build_object('user_id','user-'||i,'legal_hold',i=6) FROM generate_series(1,6) i"
            ).format(table)
        )
    client = SimpleNamespace(
        config=SimpleNamespace(backend="postgres"), backend=SimpleNamespace(conn=conn, _table_name=lambda ns: "ns_" + ns)
    )
    collector = Collection(client, "a")
    collector.store.create_policy("a", "p", "Old memories")
    collector.store.edit_rule("a", "p", "old", "add", {"max_age_days": 1, "action": "delete"})
    collector.store.put(
        "a",
        "nightly",
        ScheduleDefinition(policy_id="p", dry_run=False, spec=CronJobSpec(schedule="* * * * *", concurrencyPolicy="Forbid")),
        "admin",
        expected_revision=0,
        now=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
    )
    victim = start("victim", "mark" if phase == "heartbeat" else phase)
    eventually(lambda: "CRASH_BOUNDARY_READY" in docker("logs", victim), timeout=45)
    job = conn.execute("SELECT * FROM evolve_retention_jobs WHERE status='running'").fetchone()
    assert job
    if phase in ("mark", "heartbeat"):
        assert conn.execute("SELECT count(*) AS n FROM evolve_retention_candidates").fetchone()["n"] == 1
    else:
        assert conn.execute("SELECT count(*) AS n FROM evolve_retention_audit WHERE outcome='deleted'").fetchone()["n"] == 1
    if phase == "heartbeat":
        start("survivor-1")
        start("survivor-2")
        # Long-running work must not be mistaken for a dead executor. Other
        # schedulers keep dispatching while the independent heartbeat renews.
        time.sleep(65)
        live = conn.execute("SELECT * FROM evolve_retention_jobs WHERE job_id=%s", (job["job_id"],)).fetchone()
        assert live["status"] == "running"
        assert live["heartbeat_at"] > job["heartbeat_at"]
        assert conn.execute("SELECT count(*) AS n FROM evolve_retention_jobs").fetchone()["n"] == 1
    docker("kill", "--signal=KILL", victim)
    assert docker("inspect", "--format", "{{.State.ExitCode}}", victim) == "137"
    # Transaction two was rolled back; transaction one's outcome survived SIGKILL.
    assert conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == (6 if phase in ("mark", "heartbeat") else 5)
    if phase != "heartbeat":
        start("survivor-1")
        start("survivor-2")
    # Use the real 60-second heartbeat expiry and actual cron ticks, without
    # changing database timestamps or accelerating the recovery threshold.
    eventually(
        lambda: (
            conn.execute("SELECT status FROM evolve_retention_jobs WHERE job_id=%s", (job["job_id"],)).fetchone()["status"] == "interrupted"
        )
    )
    eventually(lambda: conn.execute("SELECT count(*) AS n FROM ns_a").fetchone()["n"] == 1)
    eventually(lambda: conn.execute("SELECT count(*) AS n FROM evolve_retention_jobs WHERE status='completed'").fetchone()["n"] >= 1)
    assert conn.execute("SELECT metadata FROM ns_a").fetchone()["metadata"]["legal_hold"]
    assert conn.execute("SELECT count(*) AS n FROM ns_b").fetchone()["n"] == 6
    assert conn.execute("SELECT count(*) AS n FROM evolve_retention_audit WHERE outcome='deleted'").fetchone()["n"] == 5
    assert conn.execute("SELECT count(*) AS n FROM evolve_retention_audit WHERE outcome='marked'").fetchone()["n"] == 6
    assert not conn.execute(
        "SELECT entity_id FROM evolve_retention_audit WHERE outcome='deleted' GROUP BY entity_id HAVING count(*)>1"
    ).fetchall()
    assert not conn.execute("SELECT scheduled_at FROM evolve_retention_jobs GROUP BY scheduled_at HAVING count(*)>1").fetchall()
    assert "literal private memory" not in str(collector.list(audit=True))
    print(
        f"PASS {phase}: SIGKILL exit 137; interrupted job recovered by later occurrence; five unique deletions; hold and other namespace preserved"
    )
