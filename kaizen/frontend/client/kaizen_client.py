import logging

from kaizen.schema.core import Entity, Namespace, RecordedEntity
from kaizen.schema.exceptions import NamespaceNotFoundException
from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.tips import ConsolidationResult
from kaizen.config.kaizen import KaizenConfig
from kaizen.backend.base import BaseEntityBackend

logger = logging.getLogger(__name__)


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

    def cluster_tips(self, namespace_id: str, threshold: float | None = None, limit: int = 10000) -> list[list[RecordedEntity]]:
        """Cluster guideline entities by task description similarity.

        Args:
            namespace_id: Namespace to fetch entities from.
            threshold: Cosine similarity threshold (0-1). Defaults to config value.
            limit: Maximum number of guideline entities to fetch for clustering.

        Returns:
            List of clusters, each containing related RecordedEntity objects.
        """
        from kaizen.llm.tips.clustering import cluster_entities

        if threshold is None:
            threshold = self.config.clustering_threshold

        entities = self.get_all_entities(namespace_id, filters={"type": "guideline"}, limit=limit)
        if len(entities) >= limit:
            logger.warning(
                "Fetched %d entities (hit limit=%d); clustering results may be incomplete. Consider increasing the limit.",
                len(entities),
                limit,
            )
        return cluster_entities(entities, threshold=threshold)

    def consolidate_tips(self, namespace_id: str, threshold: float | None = None) -> ConsolidationResult:
        """Cluster similar tips and combine each cluster into consolidated guidelines.

        Args:
            namespace_id: Namespace to consolidate entities in.
            threshold: Cosine similarity threshold (0-1). Defaults to config value.

        Returns:
            ConsolidationResult with counts of clusters, tips before, and tips after.
        """
        from kaizen.llm.tips.clustering import combine_cluster

        clusters = self.cluster_tips(namespace_id, threshold=threshold)
        clusters_found = 0
        tips_before = 0
        tips_after = 0

        for cluster in clusters:
            # Phase 1: combine + insert (skip cluster on failure)
            try:
                consolidated_tips = combine_cluster(cluster)

                task_description = (cluster[0].metadata or {}).get("task_description", "")
                new_entities = [
                    Entity(
                        content=tip.content,
                        type="guideline",
                        metadata={
                            "task_description": task_description,
                            "rationale": tip.rationale,
                            "category": tip.category,
                            "trigger": tip.trigger,
                        },
                    )
                    for tip in consolidated_tips
                ]
                if not new_entities:
                    logger.warning(
                        "LLM returned no consolidated tips for cluster (IDs: %s); skipping deletion.",
                        [e.id for e in cluster],
                    )
                    continue
                self.update_entities(namespace_id, new_entities, enable_conflict_resolution=False)
            except Exception:
                logger.warning(
                    "Failed to consolidate cluster of %d entities (IDs: %s); skipping.",
                    len(cluster),
                    [e.id for e in cluster],
                    exc_info=True,
                )
                continue

            clusters_found += 1
            tips_before += len(cluster)
            tips_after += len(consolidated_tips)

            # Phase 2: delete originals (log errors but don't roll back insert)
            for entity in cluster:
                try:
                    self.delete_entity_by_id(namespace_id, entity.id)
                except Exception:
                    logger.warning(
                        "Failed to delete original entity %s after successful insert; skipping.",
                        entity.id,
                        exc_info=True,
                    )

        return ConsolidationResult(
            clusters_found=clusters_found,
            tips_before=tips_before,
            tips_after=tips_after,
        )

    # Convenience methods for common patterns
    def namespace_exists(self, namespace_id: str) -> bool:
        """Check if a namespace exists."""
        try:
            self.backend.get_namespace_details(namespace_id)
            return True
        except NamespaceNotFoundException:
            return False
