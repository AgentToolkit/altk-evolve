from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAIZEN_")
    tips_model: str = "gpt-4o"
    conflict_resolution_model: str = "gpt-4o"
    custom_llm_provider: str | None = Field(default=None)


# to reload settings call llm_settings.__init__()
llm_settings = LLMSettings()
