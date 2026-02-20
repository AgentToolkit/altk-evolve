from typing import Any

from kaizen.config.kaizen import KaizenConfig
from kaizen.backend.base import BaseEntityBackend
from kaizen.llm.fact_extraction.fact_extraction import ExtractedFact, extract_facts_from_messages
from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.core import Entity, Namespace, RecordedEntity
from kaizen.schema.exceptions import NamespaceNotFoundException


class KaizenClient:
    """Wrapper client around kaizen entity backends."""

    def __init__(self, config: KaizenConfig | None = None):
        """Initialize the Kaizen client."""
        self.config = config or KaizenConfig()
        self.backend: BaseEntityBackend

        if self.config.backend == "milvus":
            from kaizen.backend.milvus import MilvusEntityBackend

            self.backend = MilvusEntityBackend(self.config.settings)
        elif self.config.backend == "filesystem":
            from kaizen.backend.filesystem import FilesystemEntityBackend, FilesystemSettings

            if not isinstance(self.config.settings, (FilesystemSettings, type(None))):
                raise TypeError(
                    f"Type of `config` should be `{FilesystemSettings.__name__}` or `None`, got `{type(self.config.settings).__name__}`"
                )
            self.backend = FilesystemEntityBackend(self.config.settings)
        else:
            raise NotImplementedError(f"Entity backend not implemented: {self.config.backend}")

    def ready(self) -> bool:
        """Check if the backend is healthy."""
        return self.backend.ready()

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        """Create a new namespace for entities to exist in."""
        return self.backend.create_namespace(namespace_id)

    def all_namespaces(self, limit: int = 10) -> list[Namespace]:
        """Get details about a specific namespace."""
        return self.backend.search_namespaces(limit)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        """Get details about a specific namespace."""
        return self.backend.get_namespace_details(namespace_id)

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        """Search namespace with filters."""
        return self.backend.search_namespaces(limit)

    def delete_namespace(self, namespace_id: str) -> None:
        """Delete a namespace that entities exist in."""
        self.backend.delete_namespace(namespace_id)

    def update_entities(self, namespace_id: str, entities: list[Entity], enable_conflict_resolution: bool = True) -> list[EntityUpdate]:
        """Add multiple entities to a namespace."""
        return self.backend.update_entities(namespace_id, entities, enable_conflict_resolution)

    def search_entities(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        """Search for entities in a namespace."""
        return self.backend.search_entities(namespace_id, query, filters, limit)

    def get_all_entities(self, namespace_id: str, filters: dict | None = None, limit: int = 100) -> list[RecordedEntity]:
        """Get all entities from a namespace."""
        return self.search_entities(namespace_id, query=None, filters=filters, limit=limit)

    def delete_entity_by_id(self, namespace_id: str, entity_id: str) -> None:
        """Delete a specific entity by its ID."""
        self.backend.delete_entity_by_id(namespace_id, entity_id)

    # Convenience methods for common patterns
    def namespace_exists(self, namespace_id: str) -> bool:
        """Check if a namespace exists."""
        try:
            self.backend.get_namespace_details(namespace_id)
            return True
        except NamespaceNotFoundException:
            return False

    def ensure_namespace(self, namespace_id: str) -> Namespace:
        """Get an existing namespace or create it if missing."""
        try:
            return self.get_namespace_details(namespace_id)
        except NamespaceNotFoundException:
            return self.create_namespace(namespace_id)

    async def store_user_memory(
        self,
        namespace_id: str,
        message: str,
        user_id: str,
        metadata: dict[str, Any] | None = None,
        enable_conflict_resolution: bool = False,
    ) -> list[EntityUpdate]:
        """Extract facts from a user utterance and persist them as `fact` entities."""
        if not message:
            return []

        self.ensure_namespace(namespace_id)

        base_metadata: dict[str, Any] = dict(metadata or {})
        base_metadata["user_id"] = user_id

        extracted = extract_facts_from_messages([{"role": "user", "content": message}])
        entities: list[Entity] = []
        for one in extracted:
            if isinstance(one, ExtractedFact):
                fact_metadata = dict(base_metadata)
                fact_metadata["category"] = one.category
                fact_metadata["key"] = one.key
                fact_metadata["value"] = one.value
                entities.append(Entity(type="fact", content=one.content, metadata=fact_metadata))
            else:
                entities.append(Entity(type="fact", content=str(one), metadata=dict(base_metadata)))

        if not entities:
            return []

        return self.update_entities(
            namespace_id=namespace_id,
            entities=entities,
            enable_conflict_resolution=enable_conflict_resolution,
        )

    def retrieve_user_memory(
        self,
        namespace_id: str,
        user_id: str,
        query: str | None = None,
        limit: int = 5,
    ) -> dict[str, list[dict[str, Any]]]:
        """Retrieve categorized user facts for prompt/context usage."""
        if limit <= 0 or not self.namespace_exists(namespace_id):
            return {}

        facts = self.search_entities(
            namespace_id=namespace_id,
            query=query,
            filters={"__entity_type": "fact", "metadata.user_id": user_id},
            limit=limit,
        )
        if query and not facts:
            facts = self.search_entities(
                namespace_id=namespace_id,
                query=None,
                filters={"__entity_type": "fact", "metadata.user_id": user_id},
                limit=limit,
            )
        if not facts and user_id != "default":
            facts = self.search_entities(
                namespace_id=namespace_id,
                query=query,
                filters={"__entity_type": "fact", "metadata.user_id": "default"},
                limit=limit,
            )
            if query and not facts:
                facts = self.search_entities(
                    namespace_id=namespace_id,
                    query=None,
                    filters={"__entity_type": "fact", "metadata.user_id": "default"},
                    limit=limit,
                )

        categorized_preferences: dict[str, list[dict[str, Any]]] = {}
        for fact in facts:
            metadata = fact.metadata or {}
            category = str(metadata.get("category") or "misc")
            categorized_preferences.setdefault(category, []).append(
                {
                    "id": fact.id,
                    "content": str(fact.content),
                    "key": metadata.get("key"),
                    "value": metadata.get("value"),
                }
            )

        return categorized_preferences
