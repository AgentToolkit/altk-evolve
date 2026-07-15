from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class EvolveConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVOLVE_", env_file=".env", extra="ignore")
    backend: Literal["milvus", "filesystem", "postgres"] = "filesystem"
    namespace_id: str = "evolve"
    settings: BaseSettings | None = None
    clustering_threshold: float = 0.80
    segmentation_enabled: bool = True
    # Consolidation dosage knobs (see docs: capability-dependent dosage).
    #   none     - skip consolidation entirely
    #   lossless - merge only equivalent guidelines; conserve support (default)
    #   lossy    - merge more aggressively (fewer, broader guidelines); support still conserved
    # Support-threshold *filtering* (sup2/sup3) is applied non-destructively at selection
    # time, not by deleting entities here.
    consolidation_mode: Literal["none", "lossless", "lossy"] = "lossless"
    lossy_target_num_guidelines: int = 12
    # Dosage-aware retrieval knobs (see docs: capability-dependent dosage).
    #   static    - inject the whole playbook (best for strong models; current default behavior)
    #   retrieval - inject core (support >= core_support) + top-k task-relevant guidelines
    # Default is "static" so existing get_guidelines callers are unaffected; set "retrieval"
    # to opt get_guidelines into the dosage-aware path.
    injection_mode: Literal["static", "retrieval"] = "static"
    retrieval_top_k: int = Field(default=10, ge=0)
    core_support: int = Field(default=3, ge=1)
    # Non-destructive sup2/sup3 floor on the candidate pool. Constrained to <= core_support
    # (see validator below) so it can never drop a guideline that qualifies for the core.
    min_support: int = Field(default=1, ge=1)
    retrieval_similarity_key: Literal["source_task", "guideline_text"] = "source_task"
    retrieval_near_core_thresh: float = Field(default=0.75, ge=0.0, le=1.0)
    retrieval_dedup_thresh: float = Field(default=0.90, ge=0.0, le=1.0)
    evidence_filter: Literal["all", "success", "failure"] = "all"

    @model_validator(mode="after")
    def _check_support_thresholds(self) -> "EvolveConfig":
        if self.min_support > self.core_support:
            raise ValueError(
                f"min_support ({self.min_support}) must be <= core_support ({self.core_support}); "
                "a floor above the core threshold would drop guidelines that qualify for the core."
            )
        return self


# to reload settings call evolve_config.__init__()
evolve_config = EvolveConfig()
