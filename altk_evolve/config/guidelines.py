import logging
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class GuidelinesSettings(BaseSettings):
    """Guideline-generation settings, read from `EVOLVE_`-prefixed environment variables."""

    model_config = SettingsConfigDict(env_prefix="EVOLVE_", env_file=".env", extra="ignore")

    guidelines_mode: str = "standard"
    consistency_method: str = "fast"
    debug_dir: Optional[Path] = Field(default=None)

    @field_validator("guidelines_mode", mode="before")
    @classmethod
    def coerce_invalid_mode(cls, v: str) -> str:
        """Fall back to 'standard' when EVOLVE_GUIDELINES_MODE is set to an unrecognized value."""
        if v not in ("standard", "consistency", "all"):
            logger.warning(f"Unrecognised EVOLVE_GUIDELINES_MODE value '{v}', defaulting to 'standard'")
            return "standard"
        return v

    @field_validator("consistency_method", mode="before")
    @classmethod
    def coerce_invalid_consistency_method(cls, v: str) -> str:
        """Fall back to 'fast' when EVOLVE_CONSISTENCY_METHOD is set to an unrecognized value."""
        if v not in ("accurate", "fast"):
            logger.warning(f"Unrecognised EVOLVE_CONSISTENCY_METHOD value '{v}', defaulting to 'fast'")
            return "fast"
        return v


# to reload settings call guidelines_settings.__init__()
guidelines_settings = GuidelinesSettings()
