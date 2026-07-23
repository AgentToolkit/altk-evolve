import datetime
import logging
from typing import TYPE_CHECKING, cast

from altk_evolve.backend.base import BaseEntityBackend
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity, Namespace, RecordedEntity
from altk_evolve.schema.exceptions import NamespaceAlreadyExistsException, NamespaceNotFoundException
from altk_evolve.schema.guidelines import ConsolidationResult

if TYPE_CHECKING:
    from altk_evolve.llm.guidelines.retrieval import GuidelineSelection, SimilarityKey

logger = logging.getLogger(__name__)


def _filter_by_evidence(entities: list[RecordedEntity], evidence_filter: str) -> list[RecordedEntity]:
    """Keep guidelines matching an evidence polarity. Unknown evidence (None) is always kept."""
    if evidence_filter == "success":
        allowed = {"success", "both", None}
    elif evidence_filter == "failure":
        allowed = {"failure", "both", None}
    else:  # "all"
        return entities
    return [e for e in entities if (e.metadata or {}).get("evidence") in allowed]


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

        # Initialize the memory hook seam. The seam is ALWAYS live — there is no
        # enable/disable switch; behavior is decided by which plugins resolve
        # (config + auto-discovered evolve.hooks.yaml). The CPEX PluginManager is
        # a process-wide singleton, so the seam is process-global, not per-client:
        #   - Constructing another client that resolves plugins resets the
        #     manager and REPLACES this client's plugins (initialize_hooks warns
        #     when the reset discards already-registered plugins, e.g. a PII
        #     redaction plugin an earlier client relied on).
        #   - A client that resolves NO plugins shuts the seam DOWN, so it does
        #     not inherit another client's process-global hooks — hence this is
        #     called unconditionally (initialize_hooks(no plugins) tears down and
        #     returns None). The heavy cpex import stays deferred, so the no-op
        #     path remains cheap. A configured-but-engine-missing setup instead
        #     raises here (fail-closed), never a silent no-op.
        from altk_evolve.hooks.manager import initialize_hooks

        initialize_hooks(self.config.hooks)

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

    def get_entity_by_id(self, namespace_id: str, entity_id: str) -> RecordedEntity | None:
        """Fetch a single entity by its ID. Returns None if not found."""
        results = self.search_entities(namespace_id, filters={"id": entity_id}, limit=1)
        return results[0] if results else None

    def patch_entity_metadata(self, namespace_id: str, entity_id: str, metadata_updates: dict) -> RecordedEntity:
        """Merge metadata_updates into an entity without touching content or ID."""
        return self.backend.update_entity_metadata(namespace_id, entity_id, metadata_updates)

    def record_access(self, namespace_id: str, entity_ids: list[str], when: datetime.datetime | None = None) -> None:
        """Stamp ``metadata.last_accessed`` (ISO-8601 UTC) on the given entities.

        This is the *explicit* half of the access-stamping story and the signal
        the retention engine's ``max_unused_days`` rules read (issue #275).

        Relationship to ``AccessStampPlugin``: the plugin is the automatic path
        — with hooks enabled it stamps every entity returned by a public read,
        on ``memory_post_read``. ``record_access`` is the manual path, for
        callers that do not run hooks or that want to record a *use* that was
        not a store read (for example, a memory recalled from a cache and
        actually acted on). Both funnel through the same core,
        :func:`~altk_evolve.hooks.plugins.access_stamp.build_access_stamps`, so
        the key and timestamp format are identical and one batch shares one
        stamp; enabling both is harmless (last writer wins).

        Failures on individual ids are logged and skipped rather than raised —
        recording an access must never break the flow it rode in on.
        """
        # Imported here (not at module scope) to keep the client import cheap;
        # build_access_stamps is a pure function with no cpex dependency.
        from altk_evolve.hooks.plugins.access_stamp import build_access_stamps

        moment = when if when is not None else datetime.datetime.now(datetime.UTC)
        for entity_id, patch in build_access_stamps([{"id": eid} for eid in entity_ids], now=lambda: moment):
            try:
                self.patch_entity_metadata(namespace_id, entity_id, patch)
            except Exception:
                logger.warning("record_access: failed to stamp entity %s", entity_id, exc_info=True)

    def get_public_entities(
        self,
        query: str | None = None,
        entity_type: str | None = None,
        limit: int = 100,
        exclude_namespace_ids: list[str] | None = None,
    ) -> list[RecordedEntity]:
        """Search for public entities across all namespaces.

        Args:
            query: Optional semantic search query.
            entity_type: Optional type filter (e.g. 'guideline').
            limit: Maximum total results to return.
            exclude_namespace_ids: Namespace IDs to skip (e.g. the caller's own
                namespace whose entities are already returned via a private search).
        """
        if limit <= 0:
            return []

        excluded = set(exclude_namespace_ids or [])
        all_results: list[RecordedEntity] = []
        for ns in self.search_namespaces(limit=1000):
            if ns.id in excluded:
                continue
            remaining = limit - len(all_results)
            if remaining <= 0:
                break
            filters: dict = {"metadata.visibility": "public"}
            if entity_type:
                filters["type"] = entity_type
            all_results.extend(self.search_entities(ns.id, query=query, filters=filters, limit=remaining))
        return all_results

    def cluster_guidelines(self, namespace_id: str, threshold: float | None = None, limit: int = 10000) -> list[list[RecordedEntity]]:
        """Cluster guideline entities by task description similarity.

        Args:
            namespace_id: Namespace to fetch entities from.
            threshold: Cosine similarity threshold (0-1). Defaults to config value.
            limit: Maximum number of guideline entities to fetch for clustering.

        Returns:
            List of clusters, each containing related RecordedEntity objects.
        """
        entities = self.get_all_entities(namespace_id, filters={"type": "guideline"}, limit=limit)
        if len(entities) >= limit:
            logger.warning(
                "Fetched %d entities (hit limit=%d); clustering results may be incomplete. Consider increasing the limit.",
                len(entities),
                limit,
            )
        return self._cluster_guideline_entities(entities, threshold=threshold)

    def _cluster_guideline_entities(self, entities: list[RecordedEntity], threshold: float | None = None) -> list[list[RecordedEntity]]:
        """Cluster a pre-fetched list of guideline entities by task similarity."""
        from altk_evolve.llm.guidelines.clustering import cluster_entities

        if threshold is None:
            threshold = self.config.clustering_threshold
        return cluster_entities(entities, threshold=threshold)

    def consolidate_guidelines(self, namespace_id: str, threshold: float | None = None, mode: str | None = None) -> ConsolidationResult:
        """Cluster similar guidelines and combine each cluster into consolidated guidelines.

        Consolidation is support-conserving: each consolidated guideline records how many
        source guidelines it merges (``support``) and their merged ``evidence`` polarity,
        and the total support is preserved (no advice is dropped).

        Args:
            namespace_id: Namespace to consolidate entities in.
            threshold: Cosine similarity threshold (0-1). Defaults to config value.
            mode: Consolidation mode — ``"none"`` (skip), ``"lossless"`` (default) or
                ``"lossy"`` (merge more aggressively). Defaults to ``config.consolidation_mode``.

        Returns:
            ConsolidationResult with cluster/guideline counts and total support before/after.
        """
        from altk_evolve.llm.guidelines.clustering import combine_cluster

        if mode is None:
            mode = getattr(self.config, "consolidation_mode", "lossless")
        if mode == "none":
            logger.info("consolidation_mode='none'; skipping consolidation for namespace %s.", namespace_id)
            return ConsolidationResult(clusters_found=0, guidelines_before=0, guidelines_after=0)

        combine_mode = "lossy" if mode == "lossy" else "lossless"

        # Read guidelines through the INTERNAL seam (_search_entities_impl), NOT
        # the public search_entities. Consolidation writes the fetched content
        # back and deletes the originals, so a redacting memory_post_read plugin
        # on the public read would make consolidation PERMANENTLY persist the
        # redacted view (the original stored content is lost when the originals
        # are deleted). The internal read round-trips the original stored
        # content. LLM egress during combine_cluster stays covered by
        # llm_pre_call; the write-back still fires memory_pre_write and the
        # deletes still fire memory_pre_delete.
        limit = 10000
        entities = self.backend._search_entities_impl(namespace_id, query=None, filters={"type": "guideline"}, limit=limit)
        if len(entities) >= limit:
            logger.warning(
                "Fetched %d entities (hit limit=%d); consolidation may be incomplete. Consider increasing the limit.",
                len(entities),
                limit,
            )
        clusters = self._cluster_guideline_entities(entities, threshold=threshold)
        clusters_found = 0
        guidelines_before = 0
        guidelines_after = 0
        support_before = 0
        support_after = 0

        for cluster in clusters:
            # Phase 1: combine + insert (skip cluster on failure)
            try:
                consolidated_guidelines = combine_cluster(cluster, mode=combine_mode)

                task_description = (cluster[0].metadata or {}).get("task_description", "")
                new_entities = [
                    Entity(
                        content=guideline.content,
                        type="guideline",
                        metadata={
                            "task_description": task_description,
                            "rationale": guideline.rationale,
                            "category": guideline.category,
                            "trigger": guideline.trigger,
                            "implementation_steps": guideline.implementation_steps,
                            "support": guideline.support,
                            "evidence": guideline.evidence,
                        },
                    )
                    for guideline in consolidated_guidelines
                ]
                if not new_entities:
                    logger.warning(
                        "LLM returned no consolidated guidelines for cluster (IDs: %s); skipping deletion.",
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
            guidelines_before += len(cluster)
            guidelines_after += len(consolidated_guidelines)
            support_before += sum(int((e.metadata or {}).get("support", 1) or 1) for e in cluster)
            support_after += sum(g.support for g in consolidated_guidelines)

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
            guidelines_before=guidelines_before,
            guidelines_after=guidelines_after,
            support_before=support_before,
            support_after=support_after,
        )

    def select_guidelines(
        self,
        namespace_id: str,
        task_query: str,
        top_k: int | None = None,
        core_support: int | None = None,
        min_support: int | None = None,
        evidence_filter: str | None = None,
        limit: int = 10000,
    ) -> "GuidelineSelection":
        """Select the always-on core plus the top-k task-relevant guidelines for a task.

        This is the dosage-aware alternative to injecting the whole playbook: the core
        (guidelines with support >= ``core_support``) is always returned, plus up to
        ``top_k`` further guidelines whose source task is most similar to ``task_query``.
        Unset arguments fall back to the corresponding ``config`` values.

        Args:
            namespace_id: Namespace to select guidelines from.
            task_query: The current task instruction to retrieve for.
            top_k: Max retrieved (non-core) guidelines. Defaults to ``config.retrieval_top_k``.
            core_support: Support threshold for the always-on core. Defaults to config.
            min_support: Non-destructive support floor on the candidate pool. Defaults to config.
            evidence_filter: ``"all"`` / ``"success"`` / ``"failure"``. Defaults to config.
            limit: Max guideline entities to fetch.

        Returns:
            A ``GuidelineSelection`` (core + retrieved).
        """
        from altk_evolve.llm.guidelines.retrieval import GuidelineSelection, select_guidelines

        top_k = top_k if top_k is not None else getattr(self.config, "retrieval_top_k", 10)
        core_support = core_support if core_support is not None else getattr(self.config, "core_support", 3)
        min_support = min_support if min_support is not None else getattr(self.config, "min_support", 1)
        evidence_filter = evidence_filter or getattr(self.config, "evidence_filter", "all")
        similarity_key = getattr(self.config, "retrieval_similarity_key", "source_task")
        near_core_thresh = getattr(self.config, "retrieval_near_core_thresh", 0.75)
        dedup_thresh = getattr(self.config, "retrieval_dedup_thresh", 0.90)

        entities = self.get_all_entities(namespace_id, filters={"type": "guideline"}, limit=limit)
        entities = _filter_by_evidence(entities, str(evidence_filter))
        if not entities:
            return GuidelineSelection(core=[], retrieved=[])

        return select_guidelines(
            entities,
            task_query,
            top_k=int(top_k),
            core_support=int(core_support),
            min_support=int(min_support),
            similarity_key=cast("SimilarityKey", similarity_key),
            near_core_thresh=float(near_core_thresh),
            dedup_thresh=float(dedup_thresh),
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
