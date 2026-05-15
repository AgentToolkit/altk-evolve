"""Phase 0 spike tests for MarkdownEntityBackend.

These tests verify the typed skeleton conforms to BaseEntityBackend's abstract
contract and that settings wiring works. They do NOT exercise the storage
behavior — that's the Phase 1 deliverable.
"""

import pytest

from altk_evolve.backend.markdown import MarkdownEntityBackend
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.markdown import MarkdownSettings


pytestmark = pytest.mark.unit


class TestMarkdownEntityBackendSpike:
    def test_instantiates_with_default_settings(self) -> None:
        backend = MarkdownEntityBackend()
        assert isinstance(backend.settings, MarkdownSettings)
        assert backend.settings.data_dir == "evolve_memory"

    def test_instantiates_with_custom_settings(self, tmp_path) -> None:
        cfg = MarkdownSettings(data_dir=str(tmp_path / "memory"))
        backend = MarkdownEntityBackend(cfg)
        assert backend.settings.data_dir == str(tmp_path / "memory")

    def test_rejects_wrong_settings_type(self) -> None:
        with pytest.raises(TypeError, match="requires MarkdownSettings"):
            MarkdownEntityBackend(FilesystemSettings())

    @pytest.mark.parametrize(
        "method_name,args",
        [
            ("ready", ()),
            ("details", ()),
            ("create_namespace", ()),
            ("get_namespace_details", ("ns",)),
            ("search_namespaces", ()),
            ("delete_namespace", ("ns",)),
            ("search_entities", ("ns",)),
            ("delete_entity_by_id", ("ns", "id")),
            ("_validate_namespace", ("ns",)),
            ("_add_entity", ("ns", "guideline", "content", 0, {})),
            ("_update_entity", ("ns", "id", "guideline", "content", 0, {})),
            ("_delete_entity", ("ns", "id")),
        ],
    )
    def test_stub_methods_raise_not_implemented(self, method_name: str, args: tuple) -> None:
        backend = MarkdownEntityBackend()
        with pytest.raises(NotImplementedError, match="Phase 0 spike"):
            getattr(backend, method_name)(*args)


class TestMarkdownSettings:
    def test_defaults(self) -> None:
        s = MarkdownSettings()
        assert s.data_dir == "evolve_memory"
        assert s.lock_timeout_seconds == 10.0
        assert s.enable_git_commit is False
        assert s.evolve_bot_author.startswith("evolve-bot")
        assert s.drift_check_max_age_seconds == 3600
        assert s.legacy_staleness_max_seconds == 3600

    def test_overrides(self) -> None:
        s = MarkdownSettings(
            data_dir="/tmp/x",
            lock_timeout_seconds=5.0,
            enable_git_commit=True,
        )
        assert s.data_dir == "/tmp/x"
        assert s.lock_timeout_seconds == 5.0
        assert s.enable_git_commit is True
