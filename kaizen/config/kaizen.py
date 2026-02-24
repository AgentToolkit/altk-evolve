from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class KaizenConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAIZEN_")
    backend: Literal["milvus", "filesystem"] = "milvus"
    namespace_id: str = "kaizen"
    settings: BaseSettings | None = None
    clustering_threshold: float = 0.80


# to reload settings call kaizen_config.__init__()
kaizen_config = KaizenConfig()
