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


# to reload settings call evolve_config.__init__()
evolve_config = EvolveConfig()
