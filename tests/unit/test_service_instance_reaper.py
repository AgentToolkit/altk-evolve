"""Service-instance attribution and operator purge tests."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from altk_evolve.backend.filesystem import FilesystemEntityBackend, FilesystemSettings
from altk_evolve.cli.cli import app
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.reaper.service_instance import _purge_connection
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity

pytestmark = pytest.mark.unit


def _client_with_backend(service_instance_id: str | None = None) -> tuple[EvolveClient, MagicMock]:
    client = EvolveClient.__new__(EvolveClient)
    client.config = EvolveConfig(backend="filesystem", service_instance_id=service_instance_id)
    client.backend = MagicMock()
    client.backend.update_entities.return_value = []
    return client, client.backend


def test_client_attributes_every_entity_from_configured_service_instance():
    client, backend = _client_with_backend(service_instance_id="instance-a")
    entities = [
        Entity(type="trajectory", content="one", metadata={"task_id": "task-1"}),
        Entity(type="trajectory", content="two", metadata={"service_instance_id": "spoofed"}),
    ]

    client.update_entities("tenant-a", entities, False)

    stored = backend.update_entities.call_args.args[1]
    assert [entity.metadata["service_instance_id"] for entity in stored] == ["instance-a", "instance-a"]
    assert stored[0].metadata["task_id"] == "task-1"
    assert entities[1].metadata["service_instance_id"] == "spoofed"


def test_client_uses_normalized_configured_service_instance():
    client, backend = _client_with_backend(service_instance_id=" instance-from-env ")

    client.update_entities("tenant-a", [Entity(type="fact", content="one")], False)

    stored = backend.update_entities.call_args.args[1]
    assert stored[0].metadata["service_instance_id"] == "instance-from-env"


def test_client_removes_caller_service_instance_metadata_when_unconfigured():
    client, backend = _client_with_backend()

    client.update_entities(
        "tenant-a",
        [Entity(type="fact", content="one", metadata={"service_instance_id": "spoofed"})],
        False,
    )

    stored = backend.update_entities.call_args.args[1]
    assert "service_instance_id" not in stored[0].metadata


def test_evolve_config_reads_service_instance_environment(monkeypatch):
    monkeypatch.setenv("EVOLVE_SERVICE_INSTANCE_ID", " instance-a ")

    assert EvolveConfig().service_instance_id == "instance-a"


def test_conflict_resolution_only_compares_entities_from_same_service_instance(tmp_path, monkeypatch):
    backend = FilesystemEntityBackend(FilesystemSettings(data_dir=str(tmp_path)))
    backend.create_namespace("tenant-a")
    filters_seen: list[dict | None] = []
    original_search = backend._search_entities_impl

    def capture_search(namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10):
        filters_seen.append(filters)
        return original_search(namespace_id, query, filters, limit)

    monkeypatch.setattr(backend, "_search_entities_impl", capture_search)
    entity = Entity(
        type="guideline",
        content="Keep writes scoped",
        metadata={"service_instance_id": "instance-a"},
    )
    update = EntityUpdate(id="unused", type="guideline", content=entity.content, event="NONE", metadata=entity.metadata)

    with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", return_value=[update]):
        backend.update_entities("tenant-a", [entity], enable_conflict_resolution=True)

    assert filters_seen == [{"type": "guideline", "metadata.service_instance_id": "instance-a"}]


class _FakeCursor:
    def __init__(self, conn: "_FakeConnection"):
        self.conn = conn
        self.rowcount = -1
        self._one: tuple[int] | None = None
        self._all: list[tuple[str, str]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: Any, params: tuple[str, str] | None = None) -> None:
        rendered = repr(statement)
        if isinstance(statement, str):
            assert "t.table_name LIKE 'ns\\_%'" in statement
            assert "c.column_name = 'metadata'" in statement
            self._all = [("public", name) for name in sorted(self.conn.records)]
            return

        table_name = next(name for name in self.conn.records if name in rendered)
        assert params is not None
        metadata_key, service_instance_id = params
        matches = [row for row in self.conn.records[table_name] if row.get(metadata_key) == service_instance_id]
        if "SELECT COUNT" in rendered:
            self._one = (len(matches),)
            return
        assert "DELETE FROM" in rendered
        self.conn.records[table_name] = [row for row in self.conn.records[table_name] if row.get(metadata_key) != service_instance_id]
        self.rowcount = len(matches)

    def fetchall(self) -> list[tuple[str, str]]:
        return self._all

    def fetchone(self) -> tuple[int] | None:
        return self._one


class _FakeConnection:
    def __init__(self):
        self.records = {
            "ns_tenant_a": [
                {"service_instance_id": "instance-a", "content": "remove"},
                {"service_instance_id": "instance-b", "content": "keep"},
                {"content": "legacy-unattributed"},
            ],
            "ns_tenant_b": [
                {"service_instance_id": "instance-a", "content": "remove too"},
                {"service_instance_id": "instance-b", "content": "keep too"},
            ],
        }
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_purge_deletes_target_instance_and_preserves_other_instance_records():
    conn = _FakeConnection()

    result = _purge_connection(conn, "instance-a", dry_run=False)

    assert result.deleted_records == 2
    assert result.tables == {"ns_tenant_a": 1, "ns_tenant_b": 1}
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert conn.records == {
        "ns_tenant_a": [
            {"service_instance_id": "instance-b", "content": "keep"},
            {"content": "legacy-unattributed"},
        ],
        "ns_tenant_b": [{"service_instance_id": "instance-b", "content": "keep too"}],
    }


def test_purge_dry_run_counts_without_deleting():
    conn = _FakeConnection()
    original = {name: [dict(row) for row in rows] for name, rows in conn.records.items()}

    result = _purge_connection(conn, "instance-a", dry_run=True)

    assert result.deleted_records == 2
    assert result.dry_run is True
    assert conn.records == original
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_purge_cli_requires_postgres(monkeypatch):
    import altk_evolve.config.evolve as evolve_config_module

    monkeypatch.setattr(evolve_config_module.evolve_config, "backend", "filesystem")

    result = CliRunner().invoke(app, ["purge", "service-instance", "--service-instance-id", "instance-a"])

    assert result.exit_code == 1
    assert "requires EVOLVE_BACKEND=postgres" in result.stdout


def test_purge_cli_emits_machine_readable_summary(monkeypatch):
    import altk_evolve.config.evolve as evolve_config_module
    from altk_evolve import reaper
    from altk_evolve.reaper import ServiceInstancePurgeResult

    monkeypatch.setattr(evolve_config_module.evolve_config, "backend", "postgres")
    purge = MagicMock(
        return_value=ServiceInstancePurgeResult(
            service_instance_id="instance-[red]a[/red]",
            dry_run=False,
            deleted_records=2,
            tables={"ns_tenant_a": 2},
        )
    )
    monkeypatch.setattr(reaper, "purge_service_instance_records", purge)

    result = CliRunner().invoke(app, ["purge", "service-instance", "--service-instance-id", "instance-[red]a[/red]"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "deleted_records": 2,
        "dry_run": False,
        "service_instance_id": "instance-[red]a[/red]",
        "tables": {"ns_tenant_a": 2},
    }
    purge.assert_called_once_with("instance-[red]a[/red]", dry_run=False)
