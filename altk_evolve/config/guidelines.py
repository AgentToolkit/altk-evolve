import logging
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class GuidelinesSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVOLVE_", env_file=".env", extra="ignore")

    guidelines_mode: str = "regular"
    debug_dir: Optional[Path] = Field(default=None)

    @field_validator("guidelines_mode", mode="before")
    @classmethod
    def coerce_invalid_mode(cls, v: str) -> str:
        if v not in ("regular", "consistency", "both"):
            logger.warning(f"Unrecognised EVOLVE_GUIDELINES_MODE value '{v}', defaulting to 'regular'")
            return "regular"
        return v


# to reload settings call guidelines_settings.__init__()
guidelines_settings = GuidelinesSettings()
