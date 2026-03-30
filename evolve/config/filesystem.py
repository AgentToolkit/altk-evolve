from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FilesystemSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVOLVE_")
    data_dir: str = Field(default="evolve_data", description="Directory to store JSON data files")


filesystem_settings = FilesystemSettings()
