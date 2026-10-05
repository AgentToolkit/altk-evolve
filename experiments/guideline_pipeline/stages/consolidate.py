"""Consolidate: a thin call to EvolveClient.consolidate_guidelines for one namespace.

Clustering, merging and support attribution are the library's; this stage only
resolves the effective mode and threshold and reports the result. Guidelines it
writes carry no ``sources`` on this version of Evolve, so their lineage is
reported as missing.
"""

from __future__ import annotations

from dataclasses import dataclass

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.guidelines import ConsolidationResult


@dataclass(frozen=True)
class ConsolidateReport:
    namespace_id: str
    mode: str
    threshold: float
    result: ConsolidationResult

    def summary(self) -> str:
        if self.mode == "none":
            return f"consolidate {self.namespace_id}: mode none, nothing changed"
        result = self.result
        return (
            f"consolidate {self.namespace_id} ({self.mode}, threshold {self.threshold}): {result.clusters_found} clusters merged, "
            f"{result.guidelines_before} -> {result.guidelines_after} guidelines, "
            f"support {result.support_before} -> {result.support_after}"
        )


def consolidate(client: EvolveClient, namespace_id: str, *, threshold: float | None = None, mode: str | None = None) -> ConsolidateReport:
    """Consolidate namespace_id; unset mode and threshold fall back to the client's config, as the library does."""
    client.get_namespace_details(namespace_id)  # a missing namespace is an error, not an empty run
    mode = mode or client.config.consolidation_mode
    threshold = threshold if threshold is not None else client.config.clustering_threshold
    result = client.consolidate_guidelines(namespace_id, threshold=threshold, mode=mode)
    return ConsolidateReport(namespace_id=namespace_id, mode=mode, threshold=threshold, result=result)
