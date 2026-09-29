import datetime
import json
import logging
import os
import uuid
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Lock
from dataclasses import dataclass
from typing import Callable, TypeVar

from pydantic import Field, ValidationError

from altk_evolve.backend.base import BaseEntityBackend, ConcurrentNamespaceUpdate
from altk_evolve.config.filesystem import FilesystemSettings, filesystem_settings
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity, Namespace, RecordedEntity
from altk_evolve.schema.exceptions import (
    EvolveException,
    NamespaceAlreadyExistsException,
    NamespaceNotFoundException,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("entities-db.filesystem")


class _DirectoryLock:
    """Serialize only compare-and-publish; never run hooks under this lock."""

    def __init__(self, directory: Path):
        self._thread_lock = Lock()
        self._path = directory / ".evolve-write-lock.sqlite"
        self._connection = None

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            self._connection = sqlite3.connect(self._path, timeout=300)
            self._connection.execute("BEGIN IMMEDIATE")
            return self
        except BaseException:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self._thread_lock.release()
            raise

    def __exit__(self, *exc):
        try:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
        finally:
            self._thread_lock.release()


class FilesystemNamespace(Namespace):
    """Extended Namespace with additional fields for filesystem storage."""

    entities: list[dict] = Field(default_factory=list, description="List of entity dictionaries")
    next_id: int = Field(default=1, description="Next available entity ID")


@dataclass
class _NamespaceWork:
    data: FilesystemNamespace
    active: FilesystemNamespace | None = None
    dirty: bool = False
    closed: bool = False


_T = TypeVar("_T")


class FilesystemEntityBackend(BaseEntityBackend):
    """A filesystem-based backend that stores data in JSON files.

    This backend uses simple text matching for search (no embeddings).
    """

    def __init__(self, config: FilesystemSettings | None = None):
        self.config = config or filesystem_settings
        self.data_dir = Path(self.config.data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = _DirectoryLock(self.data_dir)
        self._work: ContextVar[_NamespaceWork | None] = ContextVar("filesystem_work", default=None)

    def _current_work(self) -> _NamespaceWork | None:
        work = self._work.get()
        if work is not None and work.closed:
            raise EvolveException("The namespace transaction has finished")
        return work

    @property
    def _active_data(self) -> FilesystemNamespace | None:
        work = self._current_work()
        return work.active if work else None

    @_active_data.setter
    def _active_data(self, value: FilesystemNamespace | None):
        work = self._current_work()
        assert work is not None
        work.active = value

    def profile_repository(self):
        from altk_evolve.processing.repository import SQLiteProfileRepository

        path = os.getenv("EVOLVE_SQLITE_PATH") or os.getenv("EVOLVE_SQLITE_URI") or self.data_dir / "entities.sqlite.db"
        return SQLiteProfileRepository(path)

    @property
    def in_transaction(self) -> bool:
        return self._current_work() is not None

    @contextmanager
    def transaction(self, namespace_id: str):
        """Prepare privately; compare and atomically publish without callbacks under the lock.

        A concurrent write invalidates the entire snapshot. The caller must retry
        the transaction on ConcurrentNamespaceUpdate. Detached hook tasks are not
        supported; the synchronous bridge shares this operation's working copy.
        """
        if self._current_work() is not None:
            raise EvolveException("Nested filesystem transactions are not supported")
        before = self._load_namespace_data(namespace_id)
        work = _NamespaceWork(before.model_copy(deep=True))
        token = self._work.set(work)
        try:
            yield
        finally:
            work.closed = True
            self._work.reset(token)
        with self._lock:
            try:
                current = FilesystemNamespace.model_validate_json(self._namespace_file(namespace_id).read_text())
            except (FileNotFoundError, ValidationError) as exc:
                raise ConcurrentNamespaceUpdate(namespace_id) from exc
            if current != before:
                raise ConcurrentNamespaceUpdate(namespace_id)
            if work.dirty:
                self._save_namespace_data(namespace_id, work.data)

    def _write(self, namespace_id: str, operation: Callable[[], _T]) -> _T:
        work = self._current_work()
        if work is not None:
            if work.data.id != namespace_id:
                raise EvolveException("Filesystem transactions cannot write other namespaces")
            return operation()
        for attempt in range(3):
            try:
                with self.transaction(namespace_id):
                    result = operation()
                return result
            except ConcurrentNamespaceUpdate:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def _namespace_file(self, namespace_id: str) -> Path:
        """Get the path to a namespace's JSON file."""
        return self.data_dir / f"{namespace_id}.json"

    def _load_namespace_data(self, namespace_id: str) -> FilesystemNamespace:
        """Load namespace data from JSON file.

        Empty or corrupt files are treated as missing AND unlinked, so that a subsequent
        create_namespace() call does not trip on the stale file and raise
        NamespaceAlreadyExistsException, which would leave ensure_namespace() stuck.
        """
        work = self._current_work()
        if work is not None:
            if work.data.id != namespace_id:
                raise EvolveException("Filesystem transactions cannot read other namespaces")
            return work.data.model_copy(deep=True)
        file_path = self._namespace_file(namespace_id)
        if not file_path.exists():
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
        try:
            raw = file_path.read_text()
        except FileNotFoundError as exc:
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found") from exc
        if not raw.strip():
            logger.warning("Namespace file %s is empty (likely an interrupted write); removing and treating as missing.", file_path)
            with self._lock:
                if file_path.exists() and file_path.read_text() == raw:
                    file_path.unlink(missing_ok=True)
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
        try:
            return FilesystemNamespace.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("Namespace file %s is corrupt (%s); removing and treating as missing.", file_path, e)
            with self._lock:
                if file_path.exists() and file_path.read_text() == raw:
                    file_path.unlink(missing_ok=True)
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found") from e

    def _save_namespace_data(self, namespace_id: str, data: FilesystemNamespace):
        """Save namespace data to JSON file atomically.

        Why: Path.write_text truncates the file immediately and leaves it 0 bytes if the
        process is interrupted mid-write (SIGTERM, kill, crash). Write to a uniquely-named
        sibling tmp file and os.replace() so readers always see either the old or new
        complete file. The uuid suffix keeps concurrent writers from the same data_dir
        (e.g. CLI + MCP server) from clobbering each other's tmp file, which would cause
        FileNotFoundError at os.replace time.
        """
        work = self._current_work()
        if work is not None:
            if work.data.id != namespace_id:
                raise EvolveException("Filesystem transactions cannot write other namespaces")
            work.data = data.model_copy(deep=True)
            work.dirty = True
            return
        file_path = self._namespace_file(namespace_id)
        tmp_path = file_path.with_suffix(f"{file_path.suffix}.tmp.{uuid.uuid4().hex}")
        try:
            tmp_path.write_text(data.model_dump_json(indent=2))
            os.replace(tmp_path, file_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def ready(self) -> bool:
        """Check if the backend is healthy."""
        return True

    def details(self) -> dict:
        """Return details about the backend."""
        return {"data_dir": str(self.data_dir)}

    def _validate_namespace(self, namespace_id: str) -> None:
        file_path = self._namespace_file(namespace_id)
        if not file_path.exists():
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        """Create a new namespace for entities to exist in."""
        if self._current_work() is not None:
            raise EvolveException("Cannot create a namespace inside an entity transaction")
        namespace_id = namespace_id or "ns_" + str(uuid.uuid4()).replace("-", "_")
        file_path = self._namespace_file(namespace_id)

        with self._lock:
            if file_path.exists():
                raise NamespaceAlreadyExistsException(f'Namespace "{namespace_id}" already exists.')

            now = datetime.datetime.now(datetime.UTC)
            data = FilesystemNamespace(
                id=namespace_id,
                created_at=now,
                entities=[],
                next_id=1,
                num_entities=0,
            )
            self._save_namespace_data(namespace_id, data)

        return Namespace(id=namespace_id, created_at=now, num_entities=0)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        """Get details about a specific namespace."""
        data = self._load_namespace_data(namespace_id)
        return Namespace(
            id=data.id,
            created_at=data.created_at,
            num_entities=len(data.entities),
        )

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        """Search for namespaces."""
        namespaces = []
        for file_path in self.data_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text())
                namespaces.append(
                    Namespace(
                        id=data["id"],
                        created_at=datetime.datetime.fromisoformat(data["created_at"]),
                        num_entities=len(data["entities"]),
                    )
                )
            except (json.JSONDecodeError, KeyError, FileNotFoundError):
                continue
            if len(namespaces) >= limit:
                break
        return namespaces

    def _delete_namespace_impl(self, namespace_id: str):
        """Delete a namespace and all its entities."""
        file_path = self._namespace_file(namespace_id)
        with self._lock:
            if self._current_work() is not None:
                raise EvolveException("Cannot delete a namespace inside an entity transaction")
            if not file_path.exists():
                return  # Already deleted, no-op
            file_path.unlink()

    # ── update_entities hooks ────────────────────────────────────────

    def _add_entity(self, namespace_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> str:
        assert self._active_data is not None
        entity_id = str(self._active_data.next_id)
        self._active_data.next_id += 1
        created_at_iso = datetime.datetime.fromtimestamp(timestamp, datetime.UTC).isoformat()
        self._active_data.entities.append(
            {
                "id": entity_id,
                "type": entity_type,
                "content": content_str,
                "created_at": created_at_iso,
                "metadata": metadata,
            }
        )
        return entity_id

    def _update_entity(self, namespace_id: str, entity_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> None:
        assert self._active_data is not None
        created_at_iso = datetime.datetime.fromtimestamp(timestamp, datetime.UTC).isoformat()
        for ent in self._active_data.entities:
            if ent["id"] == entity_id:
                ent["content"] = content_str
                ent["created_at"] = created_at_iso
                ent["metadata"] = metadata
                break

    def _delete_entity(self, namespace_id: str, entity_id: str) -> None:
        assert self._active_data is not None
        self._active_data.entities = [e for e in self._active_data.entities if e["id"] != entity_id]

    def _post_update(self, namespace_id: str) -> None:
        assert self._active_data is not None
        self._active_data.num_entities = len(self._active_data.entities)
        self._save_namespace_data(namespace_id, self._active_data)
        self._active_data = None

    def _patch_entity(self, namespace_id: str, entity_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> None:
        """Patch the current working copy; the outer operation owns publication."""
        if not entity_id:
            raise ValueError(f"entity_id must be a non-empty string, got {entity_id!r}")

        def patch():
            if self._active_data is not None:
                self._update_entity(namespace_id, entity_id, entity_type, content_str, timestamp, metadata)
                return
            self._active_data = self._load_namespace_data(namespace_id)
            try:
                self._update_entity(namespace_id, entity_id, entity_type, content_str, timestamp, metadata)
                self._post_update(namespace_id)
            finally:
                self._active_data = None

        self._write(namespace_id, patch)

    def update_entity_metadata(self, namespace_id: str, entity_id: str, metadata_patch: dict) -> RecordedEntity:
        # Include the read/merge and both hooks in the optimistic operation.
        return self._write(
            namespace_id,
            lambda: super(FilesystemEntityBackend, self).update_entity_metadata(namespace_id, entity_id, metadata_patch),
        )

    def update_entities(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True,
        *,
        conflict_settings=None,
        processing_provenance: dict | None = None,
    ) -> list[EntityUpdate]:
        """Prepare hooks/conflicts outside the writer lock, then publish atomically."""

        def update():
            if self._active_data is not None:
                raise EvolveException("Recursive entity batch writes are not supported")
            self._active_data = self._load_namespace_data(namespace_id)
            try:
                return super(FilesystemEntityBackend, self).update_entities(
                    namespace_id,
                    entities,
                    enable_conflict_resolution,
                    conflict_settings=conflict_settings,
                    processing_provenance=processing_provenance,
                )
            finally:
                self._active_data = None

        return self._write(namespace_id, update)

    # ── search ───────────────────────────────────────────────────────

    def _search_entities_internal(
        self,
        data: FilesystemNamespace,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        """Internal search method that works on loaded data."""
        entities = data.entities
        filters = filters or {}

        # Apply filters
        if filters:
            filtered = []
            for ent in entities:
                match = True
                for key, value in filters.items():
                    if key.startswith("metadata."):
                        metadata_key = key.split(".", 1)[1]
                        ent_value = (ent.get("metadata") or {}).get(metadata_key)
                    else:
                        # Check top-level field first, then metadata
                        ent_value = ent.get(key)
                        if ent_value is None and ent.get("metadata"):
                            ent_value = ent["metadata"].get(key)
                    if ent_value != value:
                        match = False
                        break
                if match:
                    filtered.append(ent)
            entities = filtered

        if query is None:
            # Return all entities (up to limit)
            results = entities[:limit]
        else:
            # Simple case-insensitive text matching
            query_lower = query.lower()
            matching = []
            for ent in entities:
                content = ent.get("content", "")
                # Convert non-string content to JSON string for searching
                if not isinstance(content, str):
                    content = json.dumps(content)
                if query_lower in content.lower():
                    matching.append(ent)
            results = matching[:limit]

        return [
            RecordedEntity(
                id=str(ent["id"]),
                type=ent["type"],
                content=ent["content"],
                created_at=datetime.datetime.fromisoformat(ent["created_at"]),
                metadata=ent.get("metadata") or {},
            )
            for ent in results
        ]

    def _search_entities_impl(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        """Search for entities in a namespace."""
        # Only the owning operation sees staged state; other readers see the
        # last atomically published JSON snapshot without waiting on generation.
        if self._active_data is not None and self._active_data.id == namespace_id:
            return self._search_entities_internal(self._active_data, query, filters, limit)
        data = self._load_namespace_data(namespace_id)
        return self._search_entities_internal(data, query, filters, limit)

    def _delete_entity_by_id_impl(self, namespace_id: str, entity_id: str):
        """Delete a specific entity by its ID."""

        def delete():
            data = self._load_namespace_data(namespace_id)
            original_count = len(data.entities)
            data.entities = [e for e in data.entities if str(e["id"]) != entity_id]
            if len(data.entities) == original_count:
                raise EvolveException(f"Entity `{entity_id}` not found")
            data.num_entities = len(data.entities)
            self._save_namespace_data(namespace_id, data)

        self._write(namespace_id, delete)
