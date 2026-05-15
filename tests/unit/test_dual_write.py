"""Tests for DualWriteBackend (Phase 1 shadow-write)."""

from __future__ import annotations

import pytest

from altk_evolve.backend._dual_write import DualWriteBackend
from altk_evolve.backend.filesystem import FilesystemEntityBackend
from altk_evolve.backend.markdown import MarkdownEntityBackend
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.markdown import MarkdownSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import Entity


pytestmark = pytest.mark.unit


@pytest.fixture
def primary(tmp_path) -> MarkdownEntityBackend:
    return MarkdownEntityBackend(MarkdownSettings(data_dir=str(tmp_path / "primary")))


@pytest.fixture
def shadow(tmp_path) -> FilesystemEntityBackend:
    return FilesystemEntityBackend(FilesystemSettings(data_dir=str(tmp_path / "shadow")))


@pytest.fixture
def dual(primary, shadow) -> DualWriteBackend:
    return DualWriteBackend(primary, shadow)


class TestReadsGoToPrimary:
    def test_search_namespaces_uses_primary(self, dual, primary) -> None:
        primary.create_namespace("default")
        nss = dual.search_namespaces()
        assert {n.id for n in nss} == {"default"}

    def test_search_entities_uses_primary(self, dual, primary) -> None:
        primary.create_namespace("default")
        primary.update_entities("default", [Entity(type="x", content="from-primary")], enable_conflict_resolution=False)
        results = dual.search_entities("default")
        assert len(results) == 1
        assert results[0].content == "from-primary"


class TestWritesMirror:
    def test_create_namespace_mirrors_to_shadow(self, dual, primary, shadow) -> None:
        dual.create_namespace("default")
        assert primary.get_namespace_details("default").id == "default"
        assert shadow.get_namespace_details("default").id == "default"

    def test_update_entities_mirrors_to_shadow(self, dual, primary, shadow) -> None:
        dual.create_namespace("default")
        dual.update_entities("default", [Entity(type="x", content="hi")], enable_conflict_resolution=False)
        assert len(primary.search_entities("default")) == 1
        assert len(shadow.search_entities("default")) == 1
        assert shadow.search_entities("default")[0].content == "hi"

    def test_delete_namespace_mirrors_to_shadow(self, dual, primary, shadow) -> None:
        dual.create_namespace("default")
        dual.delete_namespace("default")
        assert primary.search_namespaces() == []
        assert shadow.search_namespaces() == []


class TestShadowBestEffort:
    def test_shadow_create_failure_does_not_block_primary(self, dual, primary, shadow, monkeypatch) -> None:
        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated shadow outage")

        monkeypatch.setattr(shadow, "create_namespace", boom)
        # Primary write succeeds, shadow fails silently.
        ns = dual.create_namespace("default")
        assert ns.id == "default"
        assert primary.get_namespace_details("default").id == "default"
        assert dual.pending_shadow_writes == 1

    def test_shadow_update_failure_does_not_block_primary(self, dual, primary, shadow, monkeypatch) -> None:
        dual.create_namespace("default")
        # Shadow's update_entities raises.
        monkeypatch.setattr(shadow, "update_entities", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        result = dual.update_entities("default", [Entity(type="x", content="y")], enable_conflict_resolution=False)
        assert [u.event for u in result] == ["ADD"]
        assert dual.pending_shadow_writes == 1

    def test_primary_failure_propagates(self, dual, primary, monkeypatch) -> None:
        monkeypatch.setattr(primary, "create_namespace", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("primary down")))
        with pytest.raises(RuntimeError, match="primary down"):
            dual.create_namespace("default")


class TestWiredViaEvolveClient:
    def test_dual_write_active_when_shadow_configured(self, tmp_path) -> None:
        cfg = EvolveConfig(
            backend="markdown",
            backend_shadow="filesystem",
            settings=MarkdownSettings(data_dir=str(tmp_path / "primary")),
            shadow_settings=FilesystemSettings(data_dir=str(tmp_path / "shadow")),
        )
        client = EvolveClient(config=cfg)
        assert isinstance(client.backend, DualWriteBackend)
        client.create_namespace("default")
        client.update_entities("default", [Entity(type="x", content="end-to-end")], enable_conflict_resolution=False)
        # Reads come from primary (markdown); also accessible via the shadow handle.
        assert len(client.search_entities("default")) == 1
        assert client.shadow_backend is not None
        assert len(client.shadow_backend.search_entities("default")) == 1

    def test_no_dual_write_when_shadow_unset(self, tmp_path) -> None:
        cfg = EvolveConfig(
            backend="markdown",
            settings=MarkdownSettings(data_dir=str(tmp_path / "primary")),
        )
        client = EvolveClient(config=cfg)
        assert not isinstance(client.backend, DualWriteBackend)
        assert client.shadow_backend is None
