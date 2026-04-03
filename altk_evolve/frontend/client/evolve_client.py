import logging
from typing import Any

from altk_evolve.backend.base import BaseEntityBackend
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.llm.fact_extraction.fact_extraction import ExtractedFact, extract_facts_from_messages
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity, Namespace, RecordedEntity
from altk_evolve.schema.exceptions import NamespaceAlreadyExistsException, NamespaceNotFoundException
from altk_evolve.schema.tips import ConsolidationResult

logger = logging.getLogger(__name__)


class EvolveClient:
    """Wrapper client around evolve entity backends."""

    def __init__(self, config: EvolveConfig | None = None):
        """Initialize the Evolve client."""
        self.config = config or EvolveConfig()
        self.backend: BaseEntityBackend

        if self.config.backend == "milvus":
            from altk_evolve.backend.milvus import MilvusEntityBackend

            self.backend = MilvusEntityBackend(self.config.settings)
        elif self.config.backend == "filesystem":
            from altk_evolve.backend.filesystem import FilesystemEntityBackend, FilesystemSettings

            if not isinstance(self.config.settings, (FilesystemSettings, type(None))):
                raise TypeError(
                    f"Type of `config` should be `{FilesystemSettings.__name__}` or `None`, got `{type(self.config.settings).__name__}`"
                )
            self.backend = FilesystemEntityBackend(self.config.settings)
        elif self.config.backend == "postgres":
            from altk_evolve.backend.postgres import PostgresEntityBackend
            from altk_evolve.config.postgres import PostgresDBSettings

            if not isinstance(self.config.settings, (PostgresDBSettings, type(None))):
                raise TypeError(
                    f"Type of `config` should be `{PostgresDBSettings.__name__}` or `None`, got `{type(self.config.settings).__name__}`"
                )
            self.backend = PostgresEntityBackend(self.config.settings)
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
        from altk_evolve.llm.tips.clustering import cluster_entities

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
        from altk_evolve.llm.tips.clustering import combine_cluster

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
                            "implementation_steps": tip.implementation_steps,
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

    def ensure_namespace(self, namespace_id: str) -> Namespace:
        """Get an existing namespace or create it if missing."""
        try:
            return self.get_namespace_details(namespace_id)
        except NamespaceNotFoundException:
            try:
                return self.create_namespace(namespace_id)
            except NamespaceAlreadyExistsException:
                return self.get_namespace_details(namespace_id)

    def store_user_facts(
        self,
        namespace_id: str,
        message: str,
        user_id: str,
        metadata: dict[str, Any] | None = None,
        enable_conflict_resolution: bool = False,
    ) -> list[EntityUpdate]:
        """Extract facts from a user utterance and persist them as `fact` entities."""
        message = (message or "").strip()
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

    def retrieve_user_facts(
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
            filters={"type": "fact", "metadata.user_id": user_id},
            limit=limit,
        )
        if query and not facts:
            facts = self.search_entities(
                namespace_id=namespace_id,
                query=None,
                filters={"type": "fact", "metadata.user_id": user_id},
                limit=limit,
            )
        if not facts and user_id != "default":
            facts = self.search_entities(
                namespace_id=namespace_id,
                query=query,
                filters={"type": "fact", "metadata.user_id": "default"},
                limit=limit,
            )
            if query and not facts:
                facts = self.search_entities(
                    namespace_id=namespace_id,
                    query=None,
                    filters={"type": "fact", "metadata.user_id": "default"},
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
