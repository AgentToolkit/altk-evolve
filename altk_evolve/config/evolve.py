from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

from altk_evolve.config.hooks import HooksConfig


class EvolveConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVOLVE_", env_file=".env", extra="ignore")
    backend: Literal["milvus", "filesystem", "postgres"] = "filesystem"
    namespace_id: str = "evolve"
    settings: BaseSettings | None = None
    clustering_threshold: float = 0.80
    segmentation_enabled: bool = True
    hooks: HooksConfig = Field(default_factory=HooksConfig)


# to reload settings call evolve_config.__init__()
evolve_config = EvolveConfig()
