from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class PostgresDBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVOLVE_PG_")
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    host: str = Field(default="localhost")
    port: int = Field(default=5432)
    user: str = Field(default="postgres")
    password: str = Field(default="postgres")
    dbname: str = Field(default="evolve")
    auto_create_db: bool = Field(default=False)
    bootstrap_db: str = Field(default="postgres")


# to reload settings call postgres_db_settings.__init__()
postgres_db_settings = PostgresDBSettings()
