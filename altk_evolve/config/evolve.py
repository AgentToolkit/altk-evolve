from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class EvolveConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVOLVE_", env_file=".env", extra="ignore")
    backend: Literal["milvus", "filesystem", "postgres", "markdown"] = "filesystem"
    backend_shadow: Literal["milvus", "filesystem", "postgres", "markdown"] | None = None
    """Optional secondary backend for shadow-writes (Phase 1 dual-write pattern).

    When set, the EvolveClient mirrors every mutating call to this secondary
    backend in addition to the primary. Reads still come from the primary.
    Used during the cutover bake-in window to keep the legacy backend warm
    and rollback-safe (see design_doc/implementation_plan.md §7).
    """
    namespace_id: str = "evolve"
    settings: BaseSettings | None = None
    clustering_threshold: float = 0.80
    segmentation_enabled: bool = True


# to reload settings call evolve_config.__init__()
evolve_config = EvolveConfig()
