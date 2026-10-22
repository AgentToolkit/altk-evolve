from __future__ import annotations

from altk_evolve.telemetry import traced

import datetime
import logging
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable, cast

from altk_evolve.backend.base import BaseEntityBackend
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity, Namespace, RecordedEntity
from altk_evolve.schema.exceptions import NamespaceAlreadyExistsException, NamespaceNotFoundException
from altk_evolve.schema.guidelines import ConsolidationResult, Guideline

if TYPE_CHECKING:
    from altk_evolve.config.llm import LLMSettings
    from altk_evolve.processing import ProcessingPlan, ProcessingResult, ProcessingManager, ProfileReference, Trajectory
    from altk_evolve.retention.service import RetentionService
    from altk_evolve.llm.guidelines.clustering import ClusteringKey
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


def _member_tasks(members: list[RecordedEntity]) -> list[str]:
    """The distinct source tasks of a cluster's members, in member order.

    A member that is itself a cross-task merge from an earlier round contributes its
    ``source_tasks``, and any member contributes its ``task_description``.
    """
    from altk_evolve.llm.guidelines.clustering import stored_tasks

    return list(dict.fromkeys(task for member in members for task in stored_tasks(member)))


def _merged_task_metadata(members: list[RecordedEntity], key: "ClusteringKey") -> dict[str, Any]:
    """The task metadata a merged guideline keeps, from its own source members.

    Clustered by task, the members were matched on their task descriptions, which are
    similar, and the first is kept. Clustered by guideline text, they may be unrelated, and
    keeping one would tie retrieval by source task to that task alone; so the description
    is kept only when the members share it. Otherwise it is left empty and every member task
    is kept in ``source_tasks``, which retrieval by source task scores by the best match.
    A member that is an earlier cross-task merge keeps its tasks in ``source_tasks`` under
    either key, so retrieval still ranks the output for each of them.
    """
    tasks = _member_tasks(members)
    if key == "guideline_text" and len(tasks) > 1:
        return {"task_description": "", "source_tasks": tasks}
    descriptions = [str(d) for member in members if (d := (member.metadata or {}).get("task_description"))]
    result: dict[str, Any] = {"task_description": descriptions[0] if descriptions else (tasks[0] if tasks else "")}
    if len(tasks) > 1 and any(isinstance((member.metadata or {}).get("source_tasks"), (list, tuple)) for member in members):
        result["source_tasks"] = tasks
    return result


class EvolveClient:
    """Wrapper client around evolve entity backends."""

    def retention(self, namespace_id: str, *, agent_id: str | None = None) -> "RetentionService":
        """Return the public retention service bound to an authorized scope."""
        from altk_evolve.retention.service import RetentionService

        return RetentionService(self, namespace_id, agent_id=agent_id)

    def __init__(
        self,
        config: EvolveConfig | None = None,
        *,
        processing: ProcessingManager | None = None,
        processing_selector: Callable[[Any], ProcessingPlan | ProfileReference | str | None] | None = None,
    ):
        """Initialize the Evolve client."""
        self._processing = processing
        self._processing_selector = processing_selector
        self._processing_lock = Lock()
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

    @property
    def processing(self) -> ProcessingManager:
        """In-process processing API; inject a manager for custom registries/repositories."""
        from altk_evolve.processing import ProcessingManager

        with self._processing_lock:
            if self._processing is None:
                self._processing = ProcessingManager(repository=self.backend.profile_repository())
            return self._processing

    @traced("evolve.trajectory.process")
    def process_trajectory(
        self,
        trajectory: Trajectory | dict,
        *,
        namespace_id: str | None = None,
        plan: ProcessingPlan | None = None,
        processing_profile: ProfileReference | str | None = None,
        context: Any = None,
    ) -> ProcessingResult:
        """Resolve configuration once for this input batch, then invoke the manager.

        Applications assign Trajectory.batch.scope to separate user/agent progress
        in a shared namespace. Explicit plans pin configuration; unpinned profile
        references and selectors are evaluated on every call.
        """
        from altk_evolve.processing import ProcessingPlan

        if plan is not None and processing_profile is not None:
            raise ValueError("Supply only one of plan or processing_profile")
        if plan is None and processing_profile is None and self._processing_selector is not None:
            selected = self._processing_selector(context)
            if isinstance(selected, ProcessingPlan):
                plan = selected
            else:
                processing_profile = selected
        if plan is None:
            plan = self.processing.resolve(processing_profile) if processing_profile is not None else self.processing.default_plan()
        return self.processing.process(trajectory, plan=plan, client=self, namespace_id=namespace_id or self.config.namespace_id)

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

    @traced("evolve.memory.update")
    def update_entities(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True,
        *,
        conflict_settings: LLMSettings | None = None,
        processing_provenance: dict | None = None,
        carry_association_times: bool = False,
    ) -> list[EntityUpdate]:
        """Add multiple entities to a namespace.

        ``carry_association_times`` is for writing source associations moved from
        existing memories (consolidation), without conflict resolution: each keeps a
        valid earlier ``associated_at``. Every other write stamps them as new.
        """
        if conflict_settings is None and processing_provenance is None and not carry_association_times:
            return self.backend.update_entities(namespace_id, entities, enable_conflict_resolution)
        return self.backend.update_entities(
            namespace_id,
            entities,
            enable_conflict_resolution,
            conflict_settings=conflict_settings,
            processing_provenance=processing_provenance,
            carry_association_times=carry_association_times,
        )

    @traced("evolve.memory.search")
    def search_entities(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        """Search for entities in a namespace."""
        return self.backend.search_entities(namespace_id, query, filters, limit)

    def get_all_entities(self, namespace_id: str, filters: dict | None = None, limit: int = 100) -> list[RecordedEntity]:
        """Get all entities from a namespace."""
        return self.search_entities(namespace_id, query=None, filters=filters, limit=limit)

    def scan_entities(self, namespace_id: str, filters: dict | None = None, limit: int = 100) -> list[RecordedEntity]:
        """Read entities for an administrative scan without recording access.

        Retention and inventory scans are not user recalls and must not refresh
        ``metadata.last_accessed`` through ``memory_post_read``. This method
        intentionally uses the backend's administrative scan seam, so callers must
        not use it for ordinary memory retrieval where read transforms and
        access auditing are expected.
        """
        return self.backend.scan_entities(namespace_id, filters=filters, limit=limit)

    def set_entity_created_at(self, namespace_id: str, entity_id: str, created_at: datetime.datetime) -> RecordedEntity:
        """Administratively stamp an imported entity and persist the result."""
        return self.backend.set_entity_created_at(namespace_id, entity_id, created_at)

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

    @traced("evolve.memory.record_access")
    def record_access(self, namespace_id: str, entity_ids: list[str], when: datetime.datetime | None = None) -> list[str]:
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

        Returns the IDs that were stamped successfully.
        """
        # Imported here (not at module scope) to keep the client import cheap;
        # build_access_stamps is a pure function with no cpex dependency.
        from altk_evolve.hooks.plugins.access_stamp import build_access_stamps

        moment = when if when is not None else datetime.datetime.now(datetime.UTC)
        updated_ids: list[str] = []
        for entity_id, patch in build_access_stamps([{"id": eid} for eid in entity_ids], now=lambda: moment):
            try:
                self.patch_entity_metadata(namespace_id, entity_id, patch)
                updated_ids.append(entity_id)
            except Exception:
                logger.warning("record_access: failed to stamp entity %s", entity_id, exc_info=True)
        return updated_ids

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

    def cluster_guidelines(
        self,
        namespace_id: str,
        threshold: float | None = None,
        limit: int = 10000,
        *,
        key: ClusteringKey | None = None,
        max_cluster_size: int | None = None,
    ) -> list[list[RecordedEntity]]:
        """Cluster guideline entities by task description or guideline text similarity.

        Args:
            namespace_id: Namespace to fetch entities from.
            threshold: Cosine similarity threshold (0-1). Defaults to config value.
            limit: Maximum number of guideline entities to fetch for clustering.
            key: ``"task_description"`` or ``"guideline_text"``. Defaults to ``config.clustering_key``.
            max_cluster_size: Split larger clusters into chunks of at most this size.
                Defaults to ``config.consolidation_max_cluster_size`` (unbounded when unset).

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
        return self._cluster_guideline_entities(entities, threshold=threshold, key=key, max_cluster_size=max_cluster_size)

    def _cluster_guideline_entities(
        self,
        entities: list[RecordedEntity],
        threshold: float | None = None,
        key: ClusteringKey | None = None,
        max_cluster_size: int | None = None,
    ) -> list[list[RecordedEntity]]:
        """Cluster a pre-fetched list of guideline entities by task or guideline text similarity.

        Clustering runs separately inside each memory scope (user, owner, agent,
        visibility), the same exact-identity rule reconciliation uses, so a cluster
        never mixes memories from different scopes. A guideline whose scope value is
        not a scalar (e.g. a list) has no well-defined scope and is left out.
        """
        from altk_evolve.llm.guidelines.clustering import cluster_entities
        from altk_evolve.schema.provenance import scope_key

        if threshold is None:
            threshold = self.config.clustering_threshold
        key = key or self._clustering_key()
        if max_cluster_size is None:
            max_cluster_size = getattr(self.config, "consolidation_max_cluster_size", None)
        by_scope: dict[tuple, list[RecordedEntity]] = {}
        for entity in entities:
            scope = scope_key(entity)
            if scope is None:
                logger.warning("Guideline %s has a non-scalar scope value; leaving it out of clustering.", entity.id)
                continue
            by_scope.setdefault(scope, []).append(entity)
        return [
            cluster
            for scoped in by_scope.values()
            for cluster in cluster_entities(scoped, threshold=threshold, key=key, max_cluster_size=max_cluster_size)
        ]

    def _clustering_key(self) -> ClusteringKey:
        return getattr(self.config, "clustering_key", "task_description")

    def consolidate_guidelines(
        self,
        namespace_id: str,
        threshold: float | None = None,
        mode: str | None = None,
        *,
        key: ClusteringKey | None = None,
        max_cluster_size: int | None = None,
        max_rounds: int | None = None,
    ) -> ConsolidationResult:
        """Cluster similar guidelines and combine each cluster into consolidated guidelines.

        Consolidation is support-conserving: each consolidated guideline records how many
        source guidelines it merges (``support``) and their merged ``evidence`` polarity,
        and the total support is preserved (no advice is dropped).

        With ``max_rounds`` above 1, consolidation re-clusters the namespace after each
        round, so guidelines that were split across chunks by ``max_cluster_size``, or that
        only became similar once merged, can merge further. It stops early after a round
        that leaves the guideline count unchanged.

        Args:
            namespace_id: Namespace to consolidate entities in.
            threshold: Cosine similarity threshold (0-1). Defaults to config value.
            mode: Consolidation mode — ``"none"`` (skip), ``"lossless"`` (default) or
                ``"lossy"`` (merge more aggressively). Defaults to ``config.consolidation_mode``.
            key: What to cluster on — ``"task_description"`` or ``"guideline_text"``.
                Defaults to ``config.clustering_key``.
            max_cluster_size: Split larger clusters into chunks of at most this size.
                Defaults to ``config.consolidation_max_cluster_size`` (unbounded when unset).
            max_rounds: Maximum number of cluster-and-merge rounds. Defaults to
                ``config.consolidation_max_rounds``.

        Returns:
            ConsolidationResult with cluster/guideline counts and total support before/after,
            summed over all rounds. ``guidelines_before`` counts only stored guidelines that
            existed before the run, not the outputs of an earlier round merged again.
        """
        if mode is None:
            mode = getattr(self.config, "consolidation_mode", "lossless")
        if mode == "none":
            logger.info("consolidation_mode='none'; skipping consolidation for namespace %s.", namespace_id)
            return ConsolidationResult(clusters_found=0, guidelines_before=0, guidelines_after=0, rounds=0)
        if max_rounds is None:
            max_rounds = getattr(self.config, "consolidation_max_rounds", 1)
        if max_rounds < 1:
            raise ValueError(f"max_rounds must be at least 1, not {max_rounds}")

        from altk_evolve.llm.guidelines.clustering import stored_support

        combine_mode = "lossy" if mode == "lossy" else "lossless"
        key = key or self._clustering_key()

        clusters_found = 0
        guidelines_before = 0
        support_before = 0
        # Net reduction summed over rounds; the "after" totals are derived from it so a
        # guideline merged in several rounds is not counted more than once.
        guidelines_removed = 0
        support_removed = 0
        # ID of each guideline written by an earlier round -> the merge that wrote it.
        produced_by: dict[str, int] = {}
        # Originals merged but not deleted (a vetoed or failed delete). Their support is
        # already counted in the merge's output, so later rounds leave them out.
        undeleted: set[str] = set()
        merges = 0
        rounds = 0

        for rounds in range(1, max_rounds + 1):
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
            entities = self.backend.scan_entities(namespace_id, filters={"type": "guideline"}, limit=limit)
            if len(entities) >= limit:
                logger.warning(
                    "Fetched %d entities (hit limit=%d); consolidation may be incomplete. Consider increasing the limit.",
                    len(entities),
                    limit,
                )
            entities = [entity for entity in entities if str(entity.id) not in undeleted]
            clusters = self._cluster_guideline_entities(entities, threshold=threshold, key=key, max_cluster_size=max_cluster_size)
            round_removed = 0
            round_produced: dict[str, int] = {}

            for cluster in clusters:
                # The outputs of one earlier merge, clustered together again: the model has
                # already decided how to merge these, so asking again only costs a call.
                origins = {produced_by.get(str(entity.id)) for entity in cluster}
                if len(origins) == 1 and None not in origins:
                    continue
                merged = self._merge_guideline_cluster(namespace_id, cluster, combine_mode, key)
                if merged is None:
                    continue
                consolidated_guidelines, written_ids, failed_deletes = merged
                merges += 1
                round_produced.update(dict.fromkeys(written_ids, merges))
                undeleted.update(failed_deletes)

                clusters_found += 1
                cluster_support = sum(stored_support(e) for e in cluster)
                fresh = [e for e in cluster if str(e.id) not in produced_by]
                guidelines_before += len(fresh)
                support_before += sum(stored_support(e) for e in fresh)
                round_removed += len(cluster) - len(consolidated_guidelines)
                support_removed += cluster_support - sum(g.support for g in consolidated_guidelines)

            guidelines_removed += round_removed
            produced_by.update(round_produced)
            if round_removed == 0:
                break

        return ConsolidationResult(
            clusters_found=clusters_found,
            guidelines_before=guidelines_before,
            guidelines_after=guidelines_before - guidelines_removed,
            support_before=support_before,
            support_after=support_before - support_removed,
            rounds=rounds,
        )

    def _merge_guideline_cluster(
        self, namespace_id: str, cluster: list[RecordedEntity], combine_mode: str, key: ClusteringKey
    ) -> tuple[list[Guideline], list[str], list[str]] | None:
        """Combine one cluster, write the result, and delete the originals.

        Returns the consolidated guidelines, the IDs they were written under, and the IDs
        of originals that could not be deleted; or ``None`` if the cluster was skipped and
        nothing was written.
        """
        from altk_evolve.llm.guidelines.clustering import combine_cluster_with_members
        from altk_evolve.schema.provenance import attach_sources, scope_key, with_association_times

        # Never merge (or show the merge model) memories from different scopes.
        # Clustering already partitions by scope; this guards the write boundary.
        scopes = {scope_key(entity) for entity in cluster}
        shared = scopes.pop() if len(scopes) == 1 else None
        if shared is None:
            logger.warning("Cluster mixes memory scopes or has a non-scalar scope (IDs: %s); skipping.", [e.id for e in cluster])
            return None
        scope = {field: value for field, value in zip(("user_id", "owner_id", "agent_id", "visibility"), shared) if value is not None}

        # Phase 1: combine + insert (skip cluster on failure)
        try:
            attributed = combine_cluster_with_members(cluster, mode=combine_mode, clustering_key=key)
            consolidated_guidelines = [guideline for guideline, _ in attributed]

            # Each member is attributed to exactly one output, so each output carries
            # the union of its own members' source associations (original times kept),
            # a task description from its own members, and the scope every member shares.
            new_entities = [
                Entity(
                    content=guideline.content,
                    type="guideline",
                    metadata=attach_sources(
                        {
                            **scope,
                            **_merged_task_metadata(members, key),
                            "rationale": guideline.rationale,
                            "category": guideline.category,
                            "trigger": guideline.trigger,
                            "implementation_steps": guideline.implementation_steps,
                            "support": guideline.support,
                            "evidence": guideline.evidence,
                        },
                        None,
                        with_association_times(members),
                        carry_association_times=True,
                    ),
                )
                for guideline, members in attributed
            ]
            if not new_entities:
                logger.warning(
                    "LLM returned no consolidated guidelines for cluster (IDs: %s); skipping deletion.",
                    [e.id for e in cluster],
                )
                return None
            written = self.update_entities(namespace_id, new_entities, enable_conflict_resolution=False, carry_association_times=True)
        except Exception:
            logger.warning(
                "Failed to consolidate cluster of %d entities (IDs: %s); skipping.",
                len(cluster),
                [e.id for e in cluster],
                exc_info=True,
            )
            return None

        # Phase 2: delete originals (log errors but don't roll back insert)
        failed_deletes: list[str] = []
        for entity in cluster:
            try:
                self.delete_entity_by_id(namespace_id, entity.id)
            except Exception:
                failed_deletes.append(str(entity.id))
                logger.warning(
                    "Failed to delete original entity %s after successful insert; skipping.",
                    entity.id,
                    exc_info=True,
                )
        return consolidated_guidelines, [str(update.id) for update in written or []], failed_deletes

    @traced("evolve.guidelines.select")
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
