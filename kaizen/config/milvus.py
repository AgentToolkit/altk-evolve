from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class MilvusDBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAIZEN_")
    uri: str = Field(default="entities.milvus.db")
    user: str = Field(default="")
    password: str = Field(default="")
    db_name: str = Field(default="")
    token: str = Field(default="")
    timeout: float | None = Field(default=None)


class MilvusOtherSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAIZEN_")
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"


# to reload settings call milvus_client_settings.__init__()
milvus_client_settings = MilvusDBSettings()
milvus_other_settings = MilvusOtherSettings()
