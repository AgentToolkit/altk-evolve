"""Consolidate: a thin call to EvolveClient.consolidate_guidelines for one namespace.

Clustering, merging and support attribution are the library's; this stage only
resolves the effective mode, threshold, clustering key, cluster size cap and round
limit, and reports the result. Guidelines it
writes carry no ``sources`` on this version of Evolve, so their lineage is
reported as missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.guidelines import ConsolidationResult

if TYPE_CHECKING:
    from altk_evolve.llm.guidelines.clustering import ClusteringKey


@dataclass(frozen=True)
class ConsolidateReport:
    namespace_id: str
    mode: str
    threshold: float
    result: ConsolidationResult
    key: str = "task_description"
    max_cluster_size: int | None = None

    def summary(self) -> str:
        if self.mode == "none":
            return f"consolidate {self.namespace_id}: mode none, nothing changed"
        result = self.result
        cap = f", max cluster size {self.max_cluster_size}" if self.max_cluster_size is not None else ""
        return (
            f"consolidate {self.namespace_id} ({self.mode}, by {self.key}, threshold {self.threshold}{cap}): "
            f"{result.clusters_found} clusters merged in {result.rounds} round(s), "
            f"{result.guidelines_before} -> {result.guidelines_after} guidelines, "
            f"support {result.support_before} -> {result.support_after}"
        )


def consolidate(
    client: EvolveClient,
    namespace_id: str,
    *,
    threshold: float | None = None,
    mode: str | None = None,
    key: str | None = None,
    max_cluster_size: int | None = None,
    max_rounds: int | None = None,
) -> ConsolidateReport:
    """Consolidate namespace_id; unset options fall back to the client's config, as the library does."""
    client.get_namespace_details(namespace_id)  # a missing namespace is an error, not an empty run
    mode = mode or client.config.consolidation_mode
    threshold = threshold if threshold is not None else client.config.clustering_threshold
    key = key or client.config.clustering_key
    if max_cluster_size is None:
        max_cluster_size = client.config.consolidation_max_cluster_size
    result = client.consolidate_guidelines(
        namespace_id,
        threshold=threshold,
        mode=mode,
        key=cast("ClusteringKey", key),
        max_cluster_size=max_cluster_size,
        max_rounds=max_rounds,
    )
    return ConsolidateReport(
        namespace_id=namespace_id, mode=mode, threshold=threshold, result=result, key=key, max_cluster_size=max_cluster_size
    )
