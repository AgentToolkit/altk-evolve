from kaizen.schema.core import Entity, Namespace, RecordedEntity
from kaizen.schema.exceptions import NamespaceNotFoundException
from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.config.kaizen import KaizenConfig

class KaizenClient:
    """Wrapper client around kaizen kata backends."""

    def __init__(self, config: KaizenConfig | None = None):
        """Initialize the Kaizen client."""
        self.config = config or KaizenConfig()
        if self.config.provider == 'milvus':
            from kaizen.backend.milvus import MilvusKataBackend
            self.backend = MilvusKataBackend(self.config.settings)
        elif self.config.provider == 'filesystem':
            from kaizen.backend.filesystem import FilesystemKataBackend
            self.backend = FilesystemKataBackend(self.config.settings)
        else:
            raise NotImplementedError(f'Kata backend not implemented for provider {self.config.provider}')

    def ready(self) -> bool:
        """Check if the backend is healthy."""
        return self.backend.ready()

    def create_namespace(
        self,
        namespace_id: str | None = None
    ) -> Namespace:
        """Create a new namespace for entities to exist in."""
        return self.backend.create_namespace(namespace_id)

    def all_namespaces(self, limit: int = 10) -> list[Namespace]:
        """Get details about a specific namespace."""
        return self.backend.search_namespaces(limit)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        """Get details about a specific namespace."""
        return self.backend.get_namespace_details(namespace_id)

    def search_namespaces(
        self,
        limit: int = 10
    ) -> list[Namespace]:
        """Search namespace with filters."""
        return self.backend.search_namespaces( limit)

    def delete_namespace(self, namespace_id: str) -> None:
        """Delete a namespace that entities exist in."""
        self.backend.delete_namespace(namespace_id)

    def update_entities(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True
    ) -> list[EntityUpdate]:
        """Add multiple entities to a namespace."""
        return self.backend.update_entities(namespace_id, entities, enable_conflict_resolution)

    def search_entities(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10
    ) -> list[RecordedEntity]:
        """Search for entities in a namespace."""
        return self.backend.search_entities(namespace_id, query, filters, limit)

    def get_all_entities(self, namespace_id: str, filters: dict | None = None, limit: int = 100) -> list[RecordedEntity]:
        """Get all entities from a namespace."""
        return self.search_entities(namespace_id, query=None, filters=filters, limit=limit)

    def delete_entity_by_id(self, namespace_id: str, entity_id: str) -> None:
        """Delete a specific entity by its ID."""
        return self.backend.delete_entity_by_id(namespace_id, entity_id)

    # Convenience methods for common patterns
    def namespace_exists(self, namespace_id: str) -> bool:
        """Check if a namespace exists."""
        try:
            self.backend.get_namespace_details(namespace_id)
            return True
        except NamespaceNotFoundException:
            return False