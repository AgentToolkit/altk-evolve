"""Phase 1 tests for MarkdownEntityBackend.

Covers: namespace CRUD, entity add/update/delete/search, filter semantics,
_active_data visibility during update_entities, atomic writes, cross-process
flock, settings validation, and conflict-resolution integration via the
inherited update_entities template method.
"""

from __future__ import annotations

import os
import threading

import pytest

from altk_evolve.backend._md_serialization import (
    deserialize_entity,
    is_valid_ulid,
)
from altk_evolve.backend.markdown import MarkdownEntityBackend
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.markdown import MarkdownSettings
from altk_evolve.schema.core import Entity
from altk_evolve.schema.exceptions import (
    NamespaceAlreadyExistsException,
    NamespaceNotFoundException,
)


pytestmark = pytest.mark.unit


@pytest.fixture
def backend(tmp_path) -> MarkdownEntityBackend:
    return MarkdownEntityBackend(MarkdownSettings(data_dir=str(tmp_path / "memory")))


def _entity(content: str = "guideline body", entity_type: str = "guideline", **metadata) -> Entity:
    return Entity(type=entity_type, content=content, metadata=metadata or {})


# ── construction & settings ────────────────────────────────────────────────


class TestConstruction:
    def test_default_settings(self) -> None:
        b = MarkdownEntityBackend()
        assert isinstance(b.settings, MarkdownSettings)

    def test_custom_settings(self, tmp_path) -> None:
        cfg = MarkdownSettings(data_dir=str(tmp_path))
        b = MarkdownEntityBackend(cfg)
        assert b.settings.data_dir == str(tmp_path)

    def test_rejects_wrong_settings(self) -> None:
        with pytest.raises(TypeError, match="requires MarkdownSettings"):
            MarkdownEntityBackend(FilesystemSettings())

    def test_creates_data_dir_on_init(self, tmp_path) -> None:
        target = tmp_path / "deep" / "memory"
        MarkdownEntityBackend(MarkdownSettings(data_dir=str(target)))
        assert target.is_dir()

    def test_ready_when_data_dir_exists(self, backend) -> None:
        assert backend.ready() is True

    def test_details_includes_backend_and_dir(self, backend) -> None:
        details = backend.details()
        assert details["backend"] == "markdown"
        assert details["data_dir"]


# ── namespace ops ──────────────────────────────────────────────────────────


class TestNamespaceOps:
    def test_create_with_auto_id(self, backend) -> None:
        ns = backend.create_namespace()
        assert is_valid_ulid(ns.id)

    def test_create_with_explicit_id(self, backend) -> None:
        ns = backend.create_namespace("default")
        assert ns.id == "default"
        assert ns.created_at.tzinfo is not None

    def test_create_duplicate_raises(self, backend) -> None:
        backend.create_namespace("default")
        with pytest.raises(NamespaceAlreadyExistsException):
            backend.create_namespace("default")

    def test_get_details_for_missing(self, backend) -> None:
        with pytest.raises(NamespaceNotFoundException):
            backend.get_namespace_details("nope")

    def test_get_details_after_create(self, backend) -> None:
        backend.create_namespace("ns1")
        details = backend.get_namespace_details("ns1")
        assert details.id == "ns1"
        assert details.num_entities == 0

    def test_search_namespaces_empty(self, backend) -> None:
        assert backend.search_namespaces() == []

    def test_search_namespaces_lists_created(self, backend) -> None:
        backend.create_namespace("alpha")
        backend.create_namespace("bravo")
        ids = sorted(n.id for n in backend.search_namespaces())
        assert ids == ["alpha", "bravo"]

    def test_search_namespaces_respects_limit(self, backend) -> None:
        for ns_id in ["a", "b", "c", "d"]:
            backend.create_namespace(ns_id)
        assert len(backend.search_namespaces(limit=2)) == 2

    def test_delete_namespace_removes_entities(self, backend) -> None:
        backend.create_namespace("default")
        backend.update_entities(
            "default",
            [_entity("rule one")],
            enable_conflict_resolution=False,
        )
        assert backend.get_namespace_details("default").num_entities == 1
        backend.delete_namespace("default")
        with pytest.raises(NamespaceNotFoundException):
            backend.get_namespace_details("default")

    def test_delete_missing_raises(self, backend) -> None:
        with pytest.raises(NamespaceNotFoundException):
            backend.delete_namespace("ghost")


# ── entity CRUD via update_entities ────────────────────────────────────────


class TestEntityCRUD:
    def test_add_entity_writes_md_file(self, backend) -> None:
        backend.create_namespace("default")
        backend.update_entities(
            "default",
            [_entity("validate inputs", category="strategy")],
            enable_conflict_resolution=False,
        )
        # MD file exists under {data_dir}/guidelines/default/{ULID}.md
        ns_dir = os.path.join(backend.settings.data_dir, "guidelines", "default")
        files = [f for f in os.listdir(ns_dir) if f.endswith(".md")]
        assert len(files) == 1
        assert is_valid_ulid(files[0][:-3])

    def test_add_round_trips_through_search(self, backend) -> None:
        backend.create_namespace("default")
        backend.update_entities(
            "default",
            [_entity("hello world", category="strategy")],
            enable_conflict_resolution=False,
        )
        results = backend.search_entities("default")
        assert len(results) == 1
        assert results[0].content == "hello world"
        assert results[0].metadata["category"] == "strategy"
        assert is_valid_ulid(results[0].id)

    def test_multiple_adds_yield_distinct_ids(self, backend) -> None:
        backend.create_namespace("default")
        for i in range(5):
            backend.update_entities(
                "default",
                [_entity(f"rule {i}", category="strategy")],
                enable_conflict_resolution=False,
            )
        ids = {e.id for e in backend.search_entities("default", limit=10)}
        assert len(ids) == 5

    def test_delete_entity_by_id(self, backend) -> None:
        backend.create_namespace("default")
        backend.update_entities(
            "default",
            [_entity("rule")],
            enable_conflict_resolution=False,
        )
        eid = backend.search_entities("default")[0].id
        backend.delete_entity_by_id("default", eid)
        assert backend.search_entities("default") == []

    def test_delete_unknown_id_is_noop(self, backend) -> None:
        backend.create_namespace("default")
        # Does not raise; just logs.
        backend.delete_entity_by_id("default", "01HXY3K2N5QPVWZ8ABCDEFGHJK")  # pragma: allowlist secret


class TestSearchFilters:
    def _seed(self, backend) -> None:
        backend.create_namespace("default")
        for kind, cat in [
            ("guideline", "strategy"),
            ("guideline", "recovery"),
            ("fact", "domain-knowledge"),
        ]:
            backend.update_entities(
                "default",
                [Entity(type=kind, content=f"{kind}-{cat}", metadata={"category": cat})],
                enable_conflict_resolution=False,
            )

    def test_filter_by_type(self, backend) -> None:
        self._seed(backend)
        guidelines = backend.search_entities("default", filters={"type": "guideline"})
        assert len(guidelines) == 2
        assert {g.type for g in guidelines} == {"guideline"}

    def test_filter_by_metadata_dot_prefix(self, backend) -> None:
        self._seed(backend)
        recovery = backend.search_entities("default", filters={"metadata.category": "recovery"})
        assert len(recovery) == 1
        assert recovery[0].metadata["category"] == "recovery"

    def test_filter_by_metadata_bare_key_fallback(self, backend) -> None:
        self._seed(backend)
        recovery = backend.search_entities("default", filters={"category": "recovery"})
        assert len(recovery) == 1
        assert recovery[0].metadata["category"] == "recovery"

    def test_query_substring_match(self, backend) -> None:
        self._seed(backend)
        results = backend.search_entities("default", query="recovery")
        # "guideline-recovery" content matches the substring search.
        assert len(results) == 1
        assert "recovery" in results[0].content

    def test_query_is_case_insensitive(self, backend) -> None:
        self._seed(backend)
        assert backend.search_entities("default", query="GUIDELINE")

    def test_search_unknown_namespace_raises(self, backend) -> None:
        with pytest.raises(NamespaceNotFoundException):
            backend.search_entities("ghost")


# ── _active_data visibility during update_entities ─────────────────────────


class TestActiveDataSemantics:
    def test_search_during_update_sees_pending_writes(self, backend, monkeypatch) -> None:
        """During update_entities, search_entities must see in-flight ADDs.

        We patch a hook on conflict_resolution to call back into search_entities
        and assert visibility, mirroring the filesystem backend's semantics.
        """
        backend.create_namespace("default")
        # Seed one existing entity.
        backend.update_entities(
            "default",
            [_entity("pre-existing", category="strategy")],
            enable_conflict_resolution=False,
        )
        # Now add a second entity and verify both are visible from inside the
        # post-update hook (which runs while _active_entities is still set).
        seen: list[int] = []
        original_post = backend._post_update

        def hook(namespace_id: str) -> None:
            assert backend._active_entities is not None
            results = backend.search_entities(namespace_id)
            seen.append(len(results))
            original_post(namespace_id)

        monkeypatch.setattr(backend, "_post_update", hook)
        backend.update_entities(
            "default",
            [_entity("second", category="strategy")],
            enable_conflict_resolution=False,
        )
        assert seen == [2]  # both pre-existing + second visible inside the hook

    def test_active_data_cleared_after_update(self, backend) -> None:
        backend.create_namespace("default")
        backend.update_entities(
            "default",
            [_entity("x")],
            enable_conflict_resolution=False,
        )
        assert backend._active_namespace is None
        assert backend._active_entities is None


# ── atomic writes ──────────────────────────────────────────────────────────


class TestAtomicWrites:
    def test_writes_via_tmp_replace(self, backend, monkeypatch) -> None:
        """Confirm temp+replace pattern is used."""
        backend.create_namespace("default")
        replaces: list[tuple[str, str]] = []
        original_replace = os.replace

        def tracker(src, dst):
            replaces.append((src, dst))
            return original_replace(src, dst)

        monkeypatch.setattr(os, "replace", tracker)
        backend.update_entities(
            "default",
            [_entity("a")],
            enable_conflict_resolution=False,
        )
        # At minimum: namespace metadata + one entity file = 2 atomic writes.
        # (Namespace was created earlier, but conflict-resolution path may write more.)
        assert len(replaces) >= 1
        for src, dst in replaces:
            assert ".tmp." in src
            assert dst.endswith(".md") or dst.endswith(".yaml")

    def test_no_partial_files_left_on_disk(self, backend) -> None:
        backend.create_namespace("default")
        backend.update_entities(
            "default",
            [_entity("x")],
            enable_conflict_resolution=False,
        )
        # No `.tmp.` artifacts in the data dir tree.
        for root, _, files in os.walk(backend.settings.data_dir):
            for f in files:
                assert ".tmp." not in f, f"leftover tmp file at {root}/{f}"


# ── cross-process locking ──────────────────────────────────────────────────


class TestNamespaceLock:
    def test_acquires_and_releases(self, backend) -> None:
        backend.create_namespace("default")
        # Just exercising the context manager without contention.
        with backend._namespace_lock("default"):
            pass
        # Verify the lockfile exists post-acquisition.
        lock_path = backend._lockfile_path("default")
        assert os.path.exists(lock_path)

    def test_threaded_serialization(self, backend) -> None:
        backend.create_namespace("default")
        results: list[int] = []

        def worker(i: int) -> None:
            backend.update_entities(
                "default",
                [_entity(f"thread-{i}")],
                enable_conflict_resolution=False,
            )
            results.append(i)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == list(range(10))
        assert len(backend.search_entities("default", limit=20)) == 10


# ── round-trip via on-disk file ────────────────────────────────────────────


class TestOnDiskFormat:
    def test_md_file_is_parseable(self, backend, tmp_path) -> None:
        backend.create_namespace("default")
        backend.update_entities(
            "default",
            [_entity("hello", category="strategy")],
            enable_conflict_resolution=False,
        )
        ns_dir = os.path.join(backend.settings.data_dir, "guidelines", "default")
        md_files = [f for f in os.listdir(ns_dir) if f.endswith(".md")]
        assert md_files
        with open(os.path.join(ns_dir, md_files[0]), encoding="utf-8") as fh:
            text = fh.read()
        # Parses cleanly via the serialization helper.
        entity, frontmatter = deserialize_entity(text)
        assert entity.content == "hello"
        assert frontmatter["namespace"] == "default"
        assert frontmatter["authority"] == "generated"
        assert frontmatter["schema"] == "guideline/v1"

    def test_corrupt_file_is_skipped_during_search(self, backend) -> None:
        backend.create_namespace("default")
        backend.update_entities(
            "default",
            [_entity("good")],
            enable_conflict_resolution=False,
        )
        ns_dir = os.path.join(backend.settings.data_dir, "guidelines", "default")
        # Drop a malformed file alongside the good one.
        with open(os.path.join(ns_dir, "BAD.md"), "w") as fh:
            fh.write("not-yaml-not-valid")
        results = backend.search_entities("default")
        assert len(results) == 1
        assert results[0].content == "good"


# ── _validate_namespace ────────────────────────────────────────────────────


class TestValidateNamespace:
    def test_raises_for_missing(self, backend) -> None:
        with pytest.raises(NamespaceNotFoundException):
            backend._validate_namespace("ghost")

    def test_passes_for_existing(self, backend) -> None:
        backend.create_namespace("default")
        backend._validate_namespace("default")  # no raise
