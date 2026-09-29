from copy import deepcopy
import datetime
import logging
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager, nullcontext
from altk_evolve.backend.writes import PreparedWrites
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from altk_evolve.processing.repository import ProfileRepository

from pydantic_settings import BaseSettings

from altk_evolve.hooks.manager import (
    MemoryPolicyViolation,
    dispatch_memory_post_read,
    dispatch_memory_pre_delete,
    dispatch_memory_pre_metadata_patch,
    dispatch_memory_pre_namespace_delete,
    dispatch_memory_pre_write,
    hooks_active,
)
from altk_evolve.hooks.types import HookType
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity, Namespace, RecordedEntity
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.utils.utils import serialize_content

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("entities-db")


class ConcurrentEntityUpdate(EvolveException):
    """A specific merge/delete target changed after preparation; nothing was committed."""


class BaseEntityBackend(ABC):
    supports_atomic_writes = False

    def __init__(self, config: BaseSettings | None = None):
        pass

    def profile_repository(self) -> "ProfileRepository":
        """Store profiles alongside existing SQLite metadata unless overridden."""
        from altk_evolve.db.sqlite_manager import SQLiteManager
        from altk_evolve.processing.repository import SQLiteProfileRepository

        return SQLiteProfileRepository(SQLiteManager().db_path)

    @property
    def in_transaction(self) -> bool:
        """Whether the current caller is inside the storage commit boundary."""
        return False

    def transaction(self, namespace_id: str) -> AbstractContextManager[None]:
        """Commit prepared writes and checkpoints together; run no model or hook work here.

        Implementations serialize the short storage operation and roll it back on
        failure. Unrelated writes during preparation do not invalidate a batch.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support atomic namespace writes")

    def get_processing_checkpoint(self, namespace_id: str, key: str) -> dict | None:
        """Read internal progress without entity hooks; None means not committed."""
        raise NotImplementedError("This backend does not support processing checkpoints")

    def _save_processing_checkpoint(self, namespace_id: str, key: str, value: dict) -> None:
        """Insert progress inside the same transaction as its processor outputs."""
        raise NotImplementedError("This backend does not support processing checkpoints")

    @abstractmethod
    def ready(self) -> bool:
        pass

    def close(self):
        pass

    @abstractmethod
    def details(self) -> dict:
        pass

    @abstractmethod
    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        pass

    @abstractmethod
    def get_namespace_details(self, namespace_id: str) -> Namespace:
        pass

    @abstractmethod
    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        pass

    # ── hook-wrapped template methods ────────────────────────────────
    #
    # The public methods below are template methods: they fire the memory
    # hooks and delegate to protected ``_*_impl`` methods. Storage-specific
    # implementations belong in the ``_impl`` variants. A transaction/retry
    # wrapper must delegate to the complete public template so hooks still run.

    def delete_namespace(self, namespace_id: str):
        """Delete a namespace. Fires memory_pre_namespace_delete; do not override — override _delete_namespace_impl."""
        dispatch_memory_pre_namespace_delete(self, namespace_id)
        self._delete_namespace_impl(namespace_id)

    @abstractmethod
    def _delete_namespace_impl(self, namespace_id: str):
        pass

    def search_entities(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        """Search entities (public API read). Fires memory_post_read on the results.

        Internal reads (conflict-resolution pre-reads, the metadata-patch
        read-before-merge) call ``_search_entities_impl`` directly and never
        fire the hook. Do not override — override _search_entities_impl.
        """
        results = self._search_entities_impl(namespace_id, query, filters, limit)
        return dispatch_memory_post_read(self, namespace_id, results, query=query, filters=filters)

    def scan_entities(self, namespace_id: str, filters: dict | None = None, limit: int = 100) -> list[RecordedEntity]:
        """Read entities for administrative work without firing read hooks."""
        return self._search_entities_impl(namespace_id, query=None, filters=filters, limit=limit)

    def set_entity_created_at(self, namespace_id: str, entity_id: str, created_at: datetime.datetime) -> RecordedEntity:
        """Set an imported entity's persisted creation time without a public-read hook.

        This is intentionally an administrative import operation. It uses the
        backend's normal patch/persist path without firing public-read hooks.
        """
        entities = self.scan_entities(namespace_id, filters={"id": entity_id}, limit=1)
        if not entities:
            raise EvolveException(f"Entity '{entity_id}' not found in namespace '{namespace_id}'")
        entity = entities[0]
        self._patch_entity(
            namespace_id,
            entity_id,
            entity.type,
            serialize_content(entity.content),
            int(created_at.timestamp()),
            dict(entity.metadata or {}),
        )
        updated = self.scan_entities(namespace_id, filters={"id": entity_id}, limit=1)
        if not updated:
            raise EvolveException(f"Entity '{entity_id}' disappeared after timestamp update")
        return updated[0]

    @abstractmethod
    def _search_entities_impl(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        pass

    def delete_entity_by_id(self, namespace_id: str, entity_id: str):
        """Delete an entity (public API). Fires memory_pre_delete; do not override — override _delete_entity_by_id_impl.

        Unified delete semantics: this method and conflict-resolution DELETE
        verdicts inside ``update_entities`` both route through
        ``_guarded_delete``, so memory_pre_delete fires for every entity
        delete issued through the backend abstraction. Veto behavior differs
        per caller: here a halting plugin propagates
        :class:`MemoryPolicyViolation` to the caller; the conflict-resolution
        executor instead skips the vetoed delete and continues the batch.
        """
        self._guarded_delete(namespace_id, entity_id, source="api")

    def _guarded_delete(
        self,
        namespace_id: str,
        entity_id: str,
        stored_entity: RecordedEntity | None = None,
        *,
        source: Literal["api", "conflict_resolution"],
    ) -> None:
        """Single guarded delete path: fire memory_pre_delete, then delete.

        Every entity delete issued through the backend abstraction goes
        through here — the public ``delete_entity_by_id`` (``source="api"``,
        dispatching to ``_delete_entity_by_id_impl``) and conflict-resolution
        DELETE verdicts inside ``update_entities``
        (``source="conflict_resolution"``, dispatching to ``_delete_entity``)
        — so a delete can never bypass the hook. Do not override.

        The payload's ``metadata`` comes from ``stored_entity`` when the
        caller already holds it (the conflict-resolution pre-read); otherwise
        it is fetched via the internal ``_search_entities_impl`` seam (no
        memory_post_read) — only when a memory_pre_delete subscriber exists,
        so the hooks-disabled path stays zero-overhead. Entity not found →
        ``metadata=None`` and the delete proceeds to the impl as before.
        """
        if hooks_active(HookType.MEMORY_PRE_DELETE):
            if stored_entity is None:
                found = self._search_entities_impl(namespace_id, filters={"id": entity_id}, limit=1)
                stored_entity = found[0] if found else None
            dispatch_memory_pre_delete(self, namespace_id, entity_id, metadata=stored_entity.metadata if stored_entity else None)
        if source == "conflict_resolution":
            self._delete_entity(namespace_id, entity_id)
        else:
            self._delete_entity_by_id_impl(namespace_id, entity_id)

    @abstractmethod
    def _delete_entity_by_id_impl(self, namespace_id: str, entity_id: str):
        pass

    # ── update_entities template method ──────────────────────────────

    @abstractmethod
    def _validate_namespace(self, namespace_id: str) -> None:
        """Raise NamespaceNotFoundException if the namespace does not exist."""
        pass

    @abstractmethod
    def _add_entity(self, namespace_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> str:
        """Insert a new entity and return its ID as a string."""
        pass

    @abstractmethod
    def _update_entity(self, namespace_id: str, entity_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> None:
        """Update an existing entity in-place."""
        pass

    @abstractmethod
    def _delete_entity(self, namespace_id: str, entity_id: str) -> None:
        """Delete an entity by ID."""
        pass

    def _post_update(self, namespace_id: str) -> None:
        """Hook called after all entity mutations are complete. No-op by default."""
        pass

    def _patch_entity(self, namespace_id: str, entity_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> None:
        """Update an existing entity in-place (fetch-merge-write helper).

        Internal (protected): reached only from ``_update_entity_metadata_impl``.
        Backends that require pre-loaded state before calling _update_entity
        (e.g. filesystem) must override this method.
        """
        self._update_entity(namespace_id, entity_id, entity_type, content_str, timestamp, metadata)

    def update_entity_metadata(self, namespace_id: str, entity_id: str, metadata_patch: dict) -> RecordedEntity:
        """Merge metadata_patch into an entity's metadata without touching content.

        Template method: fires memory_pre_metadata_patch (which may transform
        or block the patch) and delegates to ``_update_entity_metadata_impl``.
        Override _update_entity_metadata_impl for native storage. A retry wrapper
        may delegate to this entire method to include hook work in the attempt.

        The impl returns a full RecordedEntity WITH content, which callers echo
        back to the caller (e.g. MCP publish/unpublish -> the MCP client). Run
        that return value through memory_post_read so it never leaks an
        unredacted view that a public read would have transformed. This is the
        backend layer, so it covers filesystem, postgres (RETURNING) and milvus
        (query) alike. The internal read-before-merge inside the impl still uses
        ``_search_entities_impl`` (no post_read); only the RETURN value is
        transformed. The ``_in_post_read`` guard stops the access-stamp plugin
        (which calls back into update_entity_metadata) from recursing here.
        """
        metadata_patch = dispatch_memory_pre_metadata_patch(self, namespace_id, entity_id, metadata_patch)
        entity = self._update_entity_metadata_impl(namespace_id, entity_id, metadata_patch)
        transformed = dispatch_memory_post_read(self, namespace_id, [entity])
        return transformed[0] if transformed else entity

    def _update_entity_metadata_impl(self, namespace_id: str, entity_id: str, metadata_patch: dict) -> RecordedEntity:
        """Default implementation: fetch (internal read), merge, _patch_entity.

        DB-backed backends should override with a native atomic update. Uses
        ``_search_entities_impl`` so this internal read never fires
        memory_post_read (recursion guard for read-triggered plugins that
        patch metadata).
        """
        from altk_evolve.utils.utils import serialize_content

        results = self._search_entities_impl(namespace_id, filters={"id": entity_id}, limit=1)
        if not results:
            from altk_evolve.schema.exceptions import EvolveException

            raise EvolveException(f"Entity '{entity_id}' not found in namespace '{namespace_id}'")
        entity = results[0]
        merged = {**(entity.metadata or {}), **metadata_patch}
        timestamp = int(entity.created_at.timestamp())
        self._patch_entity(namespace_id, entity_id, entity.type, serialize_content(entity.content), timestamp, merged)
        return RecordedEntity(**{**entity.model_dump(), "metadata": merged})

    def _prepare_updates(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True,
        *,
        conflict_settings=None,
        processing_provenance: dict | None = None,
    ) -> PreparedWrites:
        from altk_evolve.llm.conflict_resolution.conflict_resolution import resolve_conflicts
        from altk_evolve.hooks.backend import proposed_metadata

        self._validate_namespace(namespace_id)
        if not entities:
            logger.warning("No entities to update.")
            return PreparedWrites(int(datetime.datetime.now(datetime.UTC).timestamp()))

        entity_type = entities[0].type
        if not all(entity.type == entity_type for entity in entities):
            raise EvolveException("All entities must have the same type.")

        # Fire memory_pre_write BEFORE conflict resolution so transform
        # plugins (normalization, PII redaction, ...) run before any entity
        # content is sent to an LLM.
        entities = dispatch_memory_pre_write(self, namespace_id, entities)

        now = datetime.datetime.now(datetime.UTC)
        timestamp = int(now.timestamp())
        prepared = PreparedWrites(timestamp)

        entities_with_temporary_ids: list[RecordedEntity] = []
        for i, entity in enumerate(entities):
            entity_data = entity.model_dump()
            if entity_data.get("metadata") is None:
                entity_data["metadata"] = {}
            entities_with_temporary_ids.append(
                RecordedEntity(
                    **entity_data,
                    created_at=datetime.datetime.now(datetime.UTC),
                    id=f"Unprocessed_Entity_{i}",
                )
            )

        if enable_conflict_resolution:
            old_entities: list[RecordedEntity] = []
            for entity in entities:
                query_str = serialize_content(entity.content)
                # Internal pre-read for conflict resolution — must not fire
                # memory_post_read (public-API reads only).
                old_entities.extend(
                    self._search_entities_impl(
                        namespace_id=namespace_id,
                        query=query_str,
                        filters={"type": entity_type},
                        limit=10,
                    )
                )

            stored_by_id = {entity.id: entity.model_copy(deep=True) for entity in old_entities}
            for entity in old_entities:
                entity.metadata = proposed_metadata(self, entity.id, entity.metadata)
            updates = (
                resolve_conflicts(old_entities, entities_with_temporary_ids)
                if conflict_settings is None
                else resolve_conflicts(old_entities, entities_with_temporary_ids, settings=conflict_settings)
            )
            for update in updates:
                metadata = update.metadata or {}
                if processing_provenance is not None and update.event in ("ADD", "UPDATE"):
                    previous = stored_by_id.get(update.id) if update.event == "UPDATE" else None
                    if previous is not None:
                        prior_metadata = previous.metadata or {}
                        history = deepcopy(prior_metadata.get("processing_history", []))
                        prior = prior_metadata.get("processing")
                        if prior is not None:
                            history.append(deepcopy(prior))
                        if history:
                            metadata = {**metadata, "processing_history": history}
                    metadata = {**metadata, "processing": deepcopy(processing_provenance)}
                    update.metadata = metadata
                match update.event:
                    case "ADD":
                        update.type = entity_type
                    case "UPDATE":
                        if update.id not in stored_by_id:
                            raise EvolveException(f"Conflict resolution selected an unknown entity: {update.id}")
                        update.type = entity_type
                        prepared.expected[update.id] = stored_by_id[update.id].model_copy(deep=True)
                    case "DELETE":
                        try:
                            if update.id not in stored_by_id:
                                raise EvolveException(f"Conflict resolution selected an unknown entity: {update.id}")
                            dispatch_memory_pre_delete(
                                self, namespace_id, update.id, metadata=proposed_metadata(self, update.id, stored_by_id[update.id].metadata)
                            )
                            prepared.expected[update.id] = stored_by_id[update.id].model_copy(deep=True)
                        except MemoryPolicyViolation as violation:
                            # A policy veto (e.g. legal hold) must not abort
                            # the write: skip this delete — the stored entity
                            # survives alongside its replacement — and keep
                            # processing the rest of the batch.
                            logger.warning(
                                "memory_pre_delete plugin %r vetoed conflict-resolution DELETE of entity '%s' in namespace '%s': %s. Keeping the stored entity and continuing.",
                                violation.plugin_name,
                                update.id,
                                namespace_id,
                                violation,
                            )
                            update.event = "NONE"
                            update.metadata = {
                                **(update.metadata or {}),
                                "skipped_delete": {
                                    "hook": violation.hook_type,
                                    "plugin": violation.plugin_name,
                                    "code": violation.code,
                                    "reason": violation.reason,
                                },
                            }
                    case "NONE":
                        pass
        else:
            updates = []
            for entity in entities:
                metadata = entity.metadata or {}
                if processing_provenance is not None:
                    metadata = {**metadata, "processing": deepcopy(processing_provenance)}
                entity_id = ""
                updates.append(
                    EntityUpdate(
                        id=entity_id,
                        type=entity_type,
                        content=entity.content,
                        event="ADD",
                        metadata=metadata,
                    )
                )

        prepared.updates = updates
        return prepared

    def prepare_updates(
        self, namespace_id: str, entities: list[Entity], enable_conflict_resolution: bool = True, **kwargs
    ) -> PreparedWrites:
        """Run policy hooks and optional semantic reconciliation against available memory.

        Hook metadata patches are collected, not persisted. Reads need not describe
        one namespace revision; only destructive targets are checked at commit.
        """
        from altk_evolve.hooks.backend import collect_metadata_patches

        with collect_metadata_patches(self, namespace_id) as patches:
            prepared = self._prepare_updates(namespace_id, entities, enable_conflict_resolution, **kwargs)
            prepared.patches = deepcopy(patches)
        self._prepare_storage(prepared)
        return prepared

    def _prepare_storage(self, prepared: PreparedWrites) -> None:
        """Backend-specific expensive preparation, such as computing embeddings."""

    def _apply_prepared(self, namespace_id: str, prepared: PreparedWrites) -> list[EntityUpdate]:
        updates = deepcopy(prepared.updates)
        for update in updates:
            match update.event:
                case "ADD":
                    update.id = self._add_entity(
                        namespace_id, update.type, serialize_content(update.content), prepared.timestamp, update.metadata
                    )
                case "UPDATE":
                    self._update_entity(
                        namespace_id, update.id, update.type, serialize_content(update.content), prepared.timestamp, update.metadata
                    )
                case "DELETE":
                    self._delete_entity(namespace_id, update.id)
        self._post_update(namespace_id)
        return updates

    def commit_prepared(
        self,
        namespace_id: str,
        batches: list[PreparedWrites],
        *,
        checkpoint: tuple[str, dict] | None = None,
        checkpoint_aliases: tuple[str, ...] = (),
    ) -> list[EntityUpdate] | None:
        """Atomically publish one processor's outputs and checkpoint; None means already committed.

        Source aliases are linked atomically, including on duplicate delivery.
        Models, hooks, and embeddings must have finished in prepare_updates().
        A checkpoint requires an atomic backend; ordinary untracked writes retain
        support for non-transactional backends. Only touched replacement/delete
        targets are compared, so unrelated namespace changes never cause retries.
        """
        if checkpoint is not None and not self.supports_atomic_writes:
            raise NotImplementedError("Incremental processing requires atomic namespace writes")
        context = self.transaction(namespace_id) if self.supports_atomic_writes and not self.in_transaction else nullcontext()
        with context:
            checkpoint_keys = list(dict.fromkeys((checkpoint[0], *checkpoint_aliases))) if checkpoint is not None else []
            existing = {key: self.get_processing_checkpoint(namespace_id, key) for key in checkpoint_keys}
            committed = next((value for value in existing.values() if value is not None), None)
            if committed is not None:
                for key, value in existing.items():
                    if value is None:
                        self._save_processing_checkpoint(namespace_id, key, committed)
                return None
            expected: dict[str, RecordedEntity] = {}
            for batch in batches:
                for entity_id, entity in batch.expected.items():
                    if entity_id in expected and expected[entity_id] != entity:
                        raise ConcurrentEntityUpdate(f"Conflicting prepared versions of entity {entity_id}")
                    expected[entity_id] = entity
            for entity_id, entity in expected.items():
                current = self.scan_entities(namespace_id, filters={"id": entity_id}, limit=1)
                if current != [entity]:
                    raise ConcurrentEntityUpdate(f"Entity {entity_id} changed during preparation")
            updates = [update for batch in batches for update in self._apply_prepared(namespace_id, batch)]
            deleted = {update.id for update in updates if update.event == "DELETE"}
            for batch in batches:
                for patch in batch.patches:
                    # A prepared deletion also removes any proposed metadata for that entity.
                    if patch.entity_id not in deleted:
                        self._update_entity_metadata_impl(patch.namespace_id, patch.entity_id, patch.patch)
            if checkpoint is not None:
                for key in checkpoint_keys:
                    self._save_processing_checkpoint(namespace_id, key, checkpoint[1])
            return updates

    def update_entities(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True,
        *,
        conflict_settings=None,
        processing_provenance: dict | None = None,
    ) -> list[EntityUpdate]:
        prepared = self.prepare_updates(
            namespace_id,
            entities,
            enable_conflict_resolution,
            conflict_settings=conflict_settings,
            processing_provenance=processing_provenance,
        )
        return self.commit_prepared(namespace_id, [prepared]) or []
