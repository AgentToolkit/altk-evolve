import datetime
import logging
from abc import ABC, abstractmethod
from typing import Literal

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


class BaseEntityBackend(ABC):
    def __init__(self, config: BaseSettings | None = None):
        pass

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
    # hooks and delegate to protected ``_*_impl`` methods. Backends override
    # the ``_impl`` variants ONLY, so an override can never skip a hook.

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

    def patch_entity(self, namespace_id: str, entity_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> None:
        """Update an existing entity in-place (fetch-merge-write helper).

        Backends that require pre-loaded state before calling _update_entity
        (e.g. filesystem) must override this method.
        """
        self._update_entity(namespace_id, entity_id, entity_type, content_str, timestamp, metadata)

    def update_entity_metadata(self, namespace_id: str, entity_id: str, metadata_patch: dict) -> RecordedEntity:
        """Merge metadata_patch into an entity's metadata without touching content.

        Template method: fires memory_pre_metadata_patch (which may transform
        or block the patch) and delegates to ``_update_entity_metadata_impl``.
        Do not override — override _update_entity_metadata_impl.

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
        """Default implementation: fetch (internal read), merge, patch_entity.

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
        self.patch_entity(namespace_id, entity_id, entity.type, serialize_content(entity.content), timestamp, merged)
        return RecordedEntity(**{**entity.model_dump(), "metadata": merged})

    def update_entities(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True,
    ) -> list[EntityUpdate]:
        from altk_evolve.llm.conflict_resolution.conflict_resolution import resolve_conflicts

        self._validate_namespace(namespace_id)
        if not entities:
            logger.warning("No entities to update.")
            return []

        entity_type = entities[0].type
        if not all(entity.type == entity_type for entity in entities):
            raise EvolveException("All entities must have the same type.")

        # Fire memory_pre_write BEFORE conflict resolution so transform
        # plugins (normalization, PII redaction, ...) run before any entity
        # content is sent to an LLM.
        entities = dispatch_memory_pre_write(self, namespace_id, entities)

        now = datetime.datetime.now(datetime.UTC)
        timestamp = int(now.timestamp())

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

            stored_by_id = {entity.id: entity for entity in old_entities}
            updates = resolve_conflicts(old_entities, entities_with_temporary_ids)
            for update in updates:
                content_str = serialize_content(update.content)
                metadata = update.metadata or {}
                match update.event:
                    case "ADD":
                        update.id = self._add_entity(namespace_id, entity_type, content_str, timestamp, metadata)
                    case "UPDATE":
                        self._update_entity(namespace_id, update.id, entity_type, content_str, timestamp, metadata)
                    case "DELETE":
                        try:
                            self._guarded_delete(
                                namespace_id,
                                update.id,
                                stored_entity=stored_by_id.get(update.id),
                                source="conflict_resolution",
                            )
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
                content_str = serialize_content(entity.content)
                metadata = entity.metadata or {}
                entity_id = self._add_entity(namespace_id, entity_type, content_str, timestamp, metadata)
                updates.append(
                    EntityUpdate(
                        id=entity_id,
                        type=entity_type,
                        content=entity.content,
                        event="ADD",
                        metadata=metadata,
                    )
                )

        self._post_update(namespace_id)
        return updates
