import datetime
import logging
from abc import ABC, abstractmethod

from pydantic_settings import BaseSettings

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

    @abstractmethod
    def delete_namespace(self, namespace_id: str):
        pass

    @abstractmethod
    def search_entities(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        pass

    @abstractmethod
    def delete_entity_by_id(self, namespace_id: str, entity_id: str):
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
                old_entities.extend(
                    self.search_entities(
                        namespace_id=namespace_id,
                        query=query_str,
                        filters={"type": entity_type},
                        limit=10,
                    )
                )

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
                        self._delete_entity(namespace_id, update.id)
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
