"""Tests for EvolveClient + markdown backend wiring (Phase 1).

Covers the factory branch in evolve_client.py and the new
EvolveConfig.backend Literal accepting "markdown".
"""

from __future__ import annotations

import pytest

from altk_evolve.backend.filesystem import FilesystemEntityBackend
from altk_evolve.backend.markdown import MarkdownEntityBackend
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.markdown import MarkdownSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient, _make_backend
from altk_evolve.schema.core import Entity


pytestmark = pytest.mark.unit


class TestMakeBackend:
    def test_filesystem(self) -> None:
        b = _make_backend("filesystem", None)
        assert isinstance(b, FilesystemEntityBackend)

    def test_markdown_with_default_settings(self, tmp_path) -> None:
        b = _make_backend("markdown", MarkdownSettings(data_dir=str(tmp_path)))
        assert isinstance(b, MarkdownEntityBackend)
        assert b.settings.data_dir == str(tmp_path)

    def test_markdown_with_none_settings(self) -> None:
        b = _make_backend("markdown", None)
        assert isinstance(b, MarkdownEntityBackend)

    def test_markdown_rejects_filesystem_settings(self) -> None:
        with pytest.raises(TypeError, match="markdown backend requires"):
            _make_backend("markdown", FilesystemSettings())

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="not implemented"):
            _make_backend("nonsense", None)


class TestEvolveClientWithMarkdown:
    def test_constructs_with_markdown_backend(self, tmp_path) -> None:
        cfg = EvolveConfig(
            backend="markdown",
            settings=MarkdownSettings(data_dir=str(tmp_path / "memory")),
        )
        client = EvolveClient(config=cfg)
        assert isinstance(client.backend, MarkdownEntityBackend)
        assert client.shadow_backend is None
        assert client.ready() is True

    def test_full_round_trip_via_client(self, tmp_path) -> None:
        cfg = EvolveConfig(
            backend="markdown",
            settings=MarkdownSettings(data_dir=str(tmp_path / "memory")),
        )
        client = EvolveClient(config=cfg)
        client.create_namespace("default")
        client.update_entities(
            "default",
            [Entity(type="guideline", content="x", metadata={})],
            enable_conflict_resolution=False,
        )
        results = client.search_entities("default")
        assert len(results) == 1
        assert results[0].content == "x"

    def test_shadow_backend_constructed_when_configured(self, tmp_path) -> None:
        cfg = EvolveConfig(
            backend="markdown",
            backend_shadow="filesystem",
            settings=MarkdownSettings(data_dir=str(tmp_path / "memory")),
        )
        client = EvolveClient(config=cfg)
        assert isinstance(client.backend, MarkdownEntityBackend)
        assert isinstance(client.shadow_backend, FilesystemEntityBackend)
