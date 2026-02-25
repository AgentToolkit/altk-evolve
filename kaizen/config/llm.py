import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


def _normalize_litellm_proxy_env() -> None:
    """Map LiteLLM proxy env vars to OpenAI-compatible vars consumed by LiteLLM."""
    proxy_base = os.getenv("LITELLM_PROXY_API_BASE")
    proxy_key = os.getenv("LITELLM_PROXY_API_KEY")

    if proxy_base and not (os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")):
        os.environ["OPENAI_BASE_URL"] = proxy_base
    if proxy_key and not os.getenv("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = proxy_key


def _default_model_name() -> str:
    # Reuse shared model env when Kaizen-specific model is not configured.
    return os.getenv("KAIZEN_MODEL_NAME") or os.getenv("MODEL_NAME", "gpt-4o")


def _default_custom_provider() -> str | None:
    # If an OpenAI-compatible base URL is configured, default provider to openai.
    # Explicit KAIZEN_CUSTOM_LLM_PROVIDER still has higher priority via BaseSettings.
    if os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or os.getenv("LITELLM_PROXY_API_BASE"):
        return "openai"
    return None


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAIZEN_")
    tips_model: str = Field(default_factory=_default_model_name)
    conflict_resolution_model: str = Field(default_factory=_default_model_name)
    fact_extraction_model: str = Field(default_factory=_default_model_name)
    categorization_mode: Literal["predefined", "dynamic", "hybrid"] = "predefined"
    allow_dynamic_categories: bool = False
    confirm_new_categories: bool = False
    custom_llm_provider: str | None = Field(default_factory=_default_custom_provider)


# to reload settings call llm_settings.__init__()
_normalize_litellm_proxy_env()
llm_settings = LLMSettings()
