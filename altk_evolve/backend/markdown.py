"""MarkdownEntityBackend — Phase 1 implementation.

Stores entities as `.md` files with YAML frontmatter, one file per entity.
Designed for the Paradigm-A direction: filesystem-as-storage with the
trigger index (Phase 3) handling recognition. No vector index in this
backend itself; that lands as a sibling SQLite-VSS module in Phase 3.

Layout (Phase 1 flat; Phase 4 introduces authority + category subdirs):

    {data_dir}/{type}/{namespace}/{stable_id}.md
    {data_dir}/.namespaces/{namespace}.yaml      # namespace metadata
    {data_dir}/.locks/{namespace}.lock           # cross-process locks

Concurrency:
- Per-process: a single `threading.RLock` serializes all backend mutations.
- Cross-process: `fcntl.flock` on a `.lockfile` per namespace (Phase 1
  honors a configurable timeout; default 10s).

Atomic writes: temp-file + `os.replace`. Mirrors `FilesystemEntityBackend`
(see `altk_evolve/backend/filesystem.py:82`).

`_active_data` pattern: `update_entities` pre-loads the namespace's full
entity list so `search_entities` invoked during conflict resolution sees
uncommitted state, mirroring `filesystem.py:44` semantics.

See:
- design_doc/transform_evolve.md (architecture)
- design_doc/implementation_plan.md §5 (Phase 1 deliverables)
- design_doc/markdown_schema.md (frontmatter contract)
"""

from __future__ import annotations

import datetime as _dt
import errno
import logging
import os
import threading
from contextlib import contextmanager
from typing import Iterator

import yaml  # type: ignore[import-untyped]
from pydantic_settings import BaseSettings

from altk_evolve.backend._md_serialization import (
    _isoformat_utc,
    _parse_isoformat,
    deserialize_entity,
    entity_path_for,
    new_ulid,
    serialize_entity,
)
from altk_evolve.backend.base import BaseEntityBackend
from altk_evolve.config.markdown import MarkdownSettings, markdown_settings
from altk_evolve.schema.core import Namespace, RecordedEntity
from altk_evolve.schema.exceptions import (
    NamespaceAlreadyExistsException,
    NamespaceNotFoundException,
)


logger = logging.getLogger(__name__)


# Top-level subdirectories created lazily under data_dir.
_NAMESPACES_DIR = ".namespaces"
_LOCKS_DIR = ".locks"


class MarkdownEntityBackend(BaseEntityBackend):
    """Markdown-tree entity backend. Filesystem is the storage layer."""

    settings: MarkdownSettings

    def __init__(self, config: BaseSettings | None = None) -> None:
        super().__init__(config)
        if config is None:
            self.settings = markdown_settings
        elif isinstance(config, MarkdownSettings):
            self.settings = config
        else:
            raise TypeError(f"MarkdownEntityBackend requires MarkdownSettings, got {type(config).__name__}.")
        self._lock = threading.RLock()
        # Set during update_entities so search_entities sees uncommitted writes.
        self._active_namespace: str | None = None
        self._active_entities: list[RecordedEntity] | None = None
        os.makedirs(self.settings.data_dir, exist_ok=True)
        os.makedirs(self._namespaces_meta_dir(), exist_ok=True)
        os.makedirs(self._locks_dir(), exist_ok=True)

    # ── lifecycle ────────────────────────────────────────────────────────

    def ready(self) -> bool:
        return os.path.isdir(self.settings.data_dir)

    def details(self) -> dict:
        return {
            "backend": "markdown",
            "data_dir": os.path.abspath(self.settings.data_dir),
        }

    # ── namespace ops ────────────────────────────────────────────────────

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        with self._lock:
            ns_id = namespace_id or new_ulid()
            meta_path = self._namespace_meta_path(ns_id)
            if os.path.exists(meta_path):
                raise NamespaceAlreadyExistsException(f"Namespace {ns_id!r} already exists.")
            created_at = _dt.datetime.now(tz=_dt.timezone.utc)
            meta = {"id": ns_id, "created_at": _isoformat_utc(created_at)}
            self._atomic_write_yaml(meta_path, meta)
            logger.info("created namespace %r at %s", ns_id, meta_path)
            return Namespace(id=ns_id, created_at=created_at)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        meta_path = self._namespace_meta_path(namespace_id)
        if not os.path.exists(meta_path):
            raise NamespaceNotFoundException(f"Namespace {namespace_id!r} not found.")
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = yaml.safe_load(fh) or {}
        created_at = _parse_isoformat(meta["created_at"])
        num = self._count_entities_in_namespace(namespace_id)
        return Namespace(id=namespace_id, created_at=created_at, num_entities=num)

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        out: list[Namespace] = []
        meta_dir = self._namespaces_meta_dir()
        if not os.path.isdir(meta_dir):
            return out
        names = sorted(os.listdir(meta_dir))
        for name in names:
            if not name.endswith(".yaml"):
                continue
            ns_id = name[: -len(".yaml")]
            try:
                out.append(self.get_namespace_details(ns_id))
            except NamespaceNotFoundException:
                continue
            if len(out) >= limit:
                break
        return out

    def delete_namespace(self, namespace_id: str) -> None:
        with self._lock:
            self._validate_namespace(namespace_id)
            # Remove every per-type entity directory under this namespace.
            for entity_type_dir in self._all_type_dirs():
                ns_path = os.path.join(entity_type_dir, namespace_id)
                if os.path.isdir(ns_path):
                    self._rmtree(ns_path)
            # Remove the metadata file last.
            meta_path = self._namespace_meta_path(namespace_id)
            if os.path.exists(meta_path):
                os.remove(meta_path)
            # Lock file (best-effort cleanup).
            lock_path = self._lockfile_path(namespace_id)
            if os.path.exists(lock_path):
                try:
                    os.remove(lock_path)
                except OSError:
                    pass
            logger.info("deleted namespace %r", namespace_id)

    # ── entity ops ───────────────────────────────────────────────────────

    def search_entities(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        self._validate_namespace(namespace_id)
        # Use _active_entities when running inside update_entities so callers see
        # uncommitted mutations (matches filesystem.py semantics).
        if self._active_namespace == namespace_id and self._active_entities is not None:
            entities = list(self._active_entities)
        else:
            entities = self._load_all_entities(namespace_id)

        out: list[RecordedEntity] = []
        for entity in entities:
            if not self._matches_filters(entity, filters):
                continue
            if query and not self._matches_query(entity, query):
                continue
            out.append(entity)
            if len(out) >= limit:
                break
        return out

    def delete_entity_by_id(self, namespace_id: str, entity_id: str) -> None:
        with self._lock:
            self._validate_namespace(namespace_id)
            path = self._find_entity_file(namespace_id, entity_id)
            if path is None:
                logger.warning("delete_entity_by_id: %s not found in namespace %s", entity_id, namespace_id)
                return
            os.remove(path)

    # ── update_entities template-method hooks ────────────────────────────

    def _validate_namespace(self, namespace_id: str) -> None:
        if not os.path.exists(self._namespace_meta_path(namespace_id)):
            raise NamespaceNotFoundException(f"Namespace {namespace_id!r} not found.")

    def _add_entity(
        self,
        namespace_id: str,
        entity_type: str,
        content_str: str,
        timestamp: int,
        metadata: dict,
    ) -> str:
        stable_id = new_ulid()
        created_at = _dt.datetime.fromtimestamp(timestamp, tz=_dt.timezone.utc)
        entity = RecordedEntity(
            id=stable_id,
            type=entity_type,
            content=content_str,
            metadata=metadata or {},
            created_at=created_at,
        )
        path = entity_path_for(
            data_dir=self.settings.data_dir,
            namespace_id=namespace_id,
            entity_type=entity_type,
            stable_id=stable_id,
        )
        self._write_entity_file(path, entity, namespace_id=namespace_id)
        if self._active_entities is not None and self._active_namespace == namespace_id:
            self._active_entities.append(entity)
        return stable_id

    def _update_entity(
        self,
        namespace_id: str,
        entity_id: str,
        entity_type: str,
        content_str: str,
        timestamp: int,
        metadata: dict,
    ) -> None:
        path = self._find_entity_file(namespace_id, entity_id)
        if path is None:
            # Update of a non-existent entity is treated as add (matches
            # filesystem backend's tolerance for conflict-resolution outputs).
            new_id = self._add_entity(namespace_id, entity_type, content_str, timestamp, metadata)
            logger.info("update_entity for missing %s; created %s", entity_id, new_id)
            return
        created_at = _dt.datetime.fromtimestamp(timestamp, tz=_dt.timezone.utc)
        # Preserve the existing on-disk created_at if the timestamp is suspicious.
        try:
            existing, _ = deserialize_entity(_read_text(path))
            if abs((existing.created_at - created_at).total_seconds()) < 1.0:
                created_at = existing.created_at
        except Exception:  # noqa: BLE001
            pass
        entity = RecordedEntity(
            id=entity_id,
            type=entity_type,
            content=content_str,
            metadata=metadata or {},
            created_at=created_at,
        )
        self._write_entity_file(path, entity, namespace_id=namespace_id)
        if self._active_entities is not None and self._active_namespace == namespace_id:
            self._active_entities = [entity if e.id == entity_id else e for e in self._active_entities]

    def _delete_entity(self, namespace_id: str, entity_id: str) -> None:
        path = self._find_entity_file(namespace_id, entity_id)
        if path is not None:
            os.remove(path)
        if self._active_entities is not None and self._active_namespace == namespace_id:
            self._active_entities = [e for e in self._active_entities if e.id != entity_id]

    def _post_update(self, namespace_id: str) -> None:
        # Phase 3 will write the per-namespace manifest here. Phase 1 is a no-op.
        return None

    # Override update_entities to set up the _active_data window. We don't
    # change the parent's semantics; we just bracket it with the lock + the
    # namespace preload so search_entities can see uncommitted writes.
    def update_entities(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        namespace_id = args[0] if args else kwargs.get("namespace_id")
        if not isinstance(namespace_id, str):
            return super().update_entities(*args, **kwargs)
        with self._lock, self._namespace_lock(namespace_id):
            self._active_namespace = namespace_id
            self._active_entities = self._load_all_entities(namespace_id)
            try:
                return super().update_entities(*args, **kwargs)
            finally:
                self._active_namespace = None
                self._active_entities = None

    # ── helpers ──────────────────────────────────────────────────────────

    def _namespaces_meta_dir(self) -> str:
        return os.path.join(self.settings.data_dir, _NAMESPACES_DIR)

    def _namespace_meta_path(self, namespace_id: str) -> str:
        return os.path.join(self._namespaces_meta_dir(), f"{namespace_id}.yaml")

    def _locks_dir(self) -> str:
        return os.path.join(self.settings.data_dir, _LOCKS_DIR)

    def _lockfile_path(self, namespace_id: str) -> str:
        return os.path.join(self._locks_dir(), f"{namespace_id}.lock")

    @contextmanager
    def _namespace_lock(self, namespace_id: str) -> Iterator[None]:
        """Cross-process lock via fcntl.flock on a per-namespace lockfile.

        Falls back to no-op on platforms without fcntl (e.g. Windows) — the
        in-process threading.RLock still provides single-process safety.
        """
        try:
            import fcntl
        except ImportError:
            yield
            return
        lock_path = self._lockfile_path(namespace_id)
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "a") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass

    def _atomic_write_text(self, path: str, content: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp.{os.getpid()}.{new_ulid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def _atomic_write_yaml(self, path: str, payload: dict) -> None:
        text = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False, allow_unicode=True)
        self._atomic_write_text(path, text)

    def _write_entity_file(self, path: str, entity: RecordedEntity, *, namespace_id: str) -> None:
        text = serialize_entity(entity, namespace_id=namespace_id)
        self._atomic_write_text(path, text)

    def _all_type_dirs(self) -> list[str]:
        out = []
        for name in os.listdir(self.settings.data_dir):
            if name.startswith("."):
                continue
            full = os.path.join(self.settings.data_dir, name)
            if os.path.isdir(full):
                out.append(full)
        return out

    def _namespace_type_dirs(self, namespace_id: str) -> list[str]:
        """Return existing per-type/namespace directories holding entity files."""
        out: list[str] = []
        for type_dir in self._all_type_dirs():
            ns_dir = os.path.join(type_dir, namespace_id)
            if os.path.isdir(ns_dir):
                out.append(ns_dir)
        return out

    def _load_all_entities(self, namespace_id: str) -> list[RecordedEntity]:
        out: list[RecordedEntity] = []
        for ns_dir in self._namespace_type_dirs(namespace_id):
            for fname in sorted(os.listdir(ns_dir)):
                if not fname.endswith(".md"):
                    continue
                path = os.path.join(ns_dir, fname)
                try:
                    entity, _ = deserialize_entity(_read_text(path))
                    out.append(entity)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skipping unreadable MD file %s: %s", path, exc)
        return out

    def _find_entity_file(self, namespace_id: str, entity_id: str) -> str | None:
        for ns_dir in self._namespace_type_dirs(namespace_id):
            candidate = os.path.join(ns_dir, f"{entity_id}.md")
            if os.path.exists(candidate):
                return candidate
        return None

    def _count_entities_in_namespace(self, namespace_id: str) -> int:
        count = 0
        for ns_dir in self._namespace_type_dirs(namespace_id):
            for fname in os.listdir(ns_dir):
                if fname.endswith(".md"):
                    count += 1
        return count

    @staticmethod
    def _matches_filters(entity: RecordedEntity, filters: dict | None) -> bool:
        if not filters:
            return True
        for key, value in filters.items():
            if key.startswith("metadata."):
                meta_key = key[len("metadata.") :]
                if (entity.metadata or {}).get(meta_key) != value:
                    return False
            elif key == "type":
                if entity.type != value:
                    return False
            elif key == "id":
                if entity.id != value:
                    return False
            else:
                # Unknown filter keys: try metadata fallback so callers can pass
                # bare metadata keys without the metadata. prefix.
                if (entity.metadata or {}).get(key) != value:
                    return False
        return True

    @staticmethod
    def _matches_query(entity: RecordedEntity, query: str) -> bool:
        haystack = entity.content if isinstance(entity.content, str) else str(entity.content)
        return query.lower() in haystack.lower()

    @staticmethod
    def _rmtree(path: str) -> None:
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                try:
                    os.remove(os.path.join(root, name))
                except OSError as exc:
                    if exc.errno != errno.ENOENT:
                        raise
            for name in dirs:
                try:
                    os.rmdir(os.path.join(root, name))
                except OSError:
                    pass
        try:
            os.rmdir(path)
        except OSError:
            pass


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()
