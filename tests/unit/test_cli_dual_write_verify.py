"""Tests for `evolve backend dual-write-verify` CLI command (Phase 1)."""

from __future__ import annotations

from typing import Iterator

import pytest
from typer.testing import CliRunner

from altk_evolve.backend._dual_write import DualWriteBackend
from altk_evolve.backend.filesystem import FilesystemEntityBackend
from altk_evolve.backend.markdown import MarkdownEntityBackend
from altk_evolve.cli.cli import app
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.markdown import MarkdownSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import Entity


pytestmark = pytest.mark.unit
runner = CliRunner()


@pytest.fixture
def dual_client(tmp_path, monkeypatch) -> Iterator[EvolveClient]:
    """Build a DualWrite-backed EvolveClient and patch get_client() to return it."""
    cfg = EvolveConfig(
        backend="markdown",
        backend_shadow="filesystem",
        settings=MarkdownSettings(data_dir=str(tmp_path / "primary")),
        shadow_settings=FilesystemSettings(data_dir=str(tmp_path / "shadow")),
    )
    client = EvolveClient(config=cfg)
    monkeypatch.setattr("altk_evolve.cli.cli.get_client", lambda: client)
    yield client


class TestDualWriteVerify:
    def test_reports_in_sync_when_no_drift(self, dual_client) -> None:
        dual_client.create_namespace("default")
        for content in ["a", "b", "c"]:
            dual_client.update_entities(
                "default",
                [Entity(type="guideline", content=content, metadata={})],
                enable_conflict_resolution=False,
            )
        result = runner.invoke(app, ["backend", "dual-write-verify", "default"])
        assert result.exit_code == 0, result.stdout
        assert "in sync" in result.stdout.lower()

    def test_detects_count_drift(self, dual_client, monkeypatch) -> None:
        dual_client.create_namespace("default")
        # Write to primary directly, bypassing dual-write, to inject drift.
        assert isinstance(dual_client.backend, DualWriteBackend)
        primary = dual_client.backend.primary
        assert isinstance(primary, MarkdownEntityBackend)
        primary.update_entities(
            "default",
            [Entity(type="guideline", content="primary-only", metadata={})],
            enable_conflict_resolution=False,
        )
        result = runner.invoke(app, ["backend", "dual-write-verify", "default"])
        assert result.exit_code == 1
        assert "drift" in result.stdout.lower()
        assert "only in primary" in result.stdout.lower()

    def test_in_sync_after_dual_write_only(self, dual_client) -> None:
        dual_client.create_namespace("default")
        for content in ["a", "b", "c"]:
            dual_client.update_entities(
                "default",
                [Entity(type="guideline", content=content, metadata={"k": "v"})],
                enable_conflict_resolution=False,
            )
        result = runner.invoke(app, ["backend", "dual-write-verify", "default"])
        assert result.exit_code == 0, result.stdout
        assert "in sync" in result.stdout.lower()

    def test_detects_content_drift(self, dual_client) -> None:
        dual_client.create_namespace("default")
        dual_client.update_entities(
            "default",
            [Entity(type="guideline", content="original", metadata={})],
            enable_conflict_resolution=False,
        )
        # Tamper with the shadow's copy so IDs align but content diverges.
        assert isinstance(dual_client.backend, DualWriteBackend)
        shadow = dual_client.backend.shadow
        assert isinstance(shadow, FilesystemEntityBackend)
        shadow_entity = shadow.search_entities("default")[0]
        shadow.patch_entity(
            "default",
            shadow_entity.id,
            shadow_entity.type,
            "tampered-content",
            int(shadow_entity.created_at.timestamp()),
            shadow_entity.metadata,
        )
        result = runner.invoke(app, ["backend", "dual-write-verify", "default"])
        assert result.exit_code == 1, result.stdout
        assert "content drift" in result.stdout.lower()

    def test_aborts_when_dual_write_inactive(self, tmp_path, monkeypatch) -> None:
        # Configure a markdown-only client (no shadow).
        cfg = EvolveConfig(
            backend="markdown",
            settings=MarkdownSettings(data_dir=str(tmp_path / "primary")),
        )
        client = EvolveClient(config=cfg)
        client.create_namespace("default")
        monkeypatch.setattr("altk_evolve.cli.cli.get_client", lambda: client)
        result = runner.invoke(app, ["backend", "dual-write-verify", "default"])
        assert result.exit_code == 2
        assert "dual-write is not active" in result.stdout.lower()

    def test_aborts_on_missing_namespace(self, dual_client) -> None:
        result = runner.invoke(app, ["backend", "dual-write-verify", "ghost"])
        assert result.exit_code == 1
        assert "namespace not found" in result.stdout.lower()
