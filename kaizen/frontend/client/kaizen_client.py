from kaizen.schema.core import Entity, Namespace, RecordedEntity
from kaizen.schema.exceptions import NamespaceNotFoundException
from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.config.kaizen import KaizenConfig
from kaizen.backend.base import BaseEntityBackend


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

    def cluster_tips(
        self, namespace_id: str, threshold: float | None = None
    ) -> list[list[RecordedEntity]]:
        """Cluster guideline entities by task description similarity.

        Args:
            namespace_id: Namespace to fetch entities from.
            threshold: Cosine similarity threshold (0-1). Defaults to config value.

        Returns:
            List of clusters, each containing related RecordedEntity objects.
        """
        from kaizen.llm.tips.clustering import cluster_entities

        if threshold is None:
            threshold = self.config.clustering_threshold

        entities = self.get_all_entities(namespace_id, filters={"type": "guideline"}, limit=10000)
        return cluster_entities(entities, threshold=threshold)

    # Convenience methods for common patterns
    def namespace_exists(self, namespace_id: str) -> bool:
        """Check if a namespace exists."""
        try:
            self.backend.get_namespace_details(namespace_id)
            return True
        except NamespaceNotFoundException:
            return False
