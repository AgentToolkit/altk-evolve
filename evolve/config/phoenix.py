from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PhoenixSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PHOENIX_")
    url: str = Field(default="http://localhost:6006", description="Phoenix server URL")
    project: str = Field(default="default", description="Phoenix project name")


phoenix_settings = PhoenixSettings()
