from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class EvolveConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVOLVE_")
    backend: Literal["milvus", "filesystem", "postgres"] = "filesystem"
    namespace_id: str = "evolve"
    settings: BaseSettings | None = None
    clustering_threshold: float = 0.80


# to reload settings call evolve_config.__init__()
evolve_config = EvolveConfig()
