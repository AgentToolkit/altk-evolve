import logging
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self

logger = logging.getLogger(__name__)


class GuidelinesSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVOLVE_", env_file=".env", extra="ignore")

    guidelines_mode: str = "regular"
    debug_dir: Optional[Path] = Field(default=None)
    skip_on_no_uncertainty: bool = True
    high_uncertainty_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    low_uncertainty_threshold: float = Field(default=0.1, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def low_must_not_exceed_high(self) -> Self:
        if self.low_uncertainty_threshold > self.high_uncertainty_threshold:
            raise ValueError(
                f"EVOLVE_LOW_UNCERTAINTY_THRESHOLD ({self.low_uncertainty_threshold}) "
                f"must be <= EVOLVE_HIGH_UNCERTAINTY_THRESHOLD ({self.high_uncertainty_threshold})"
            )
        return self

    @field_validator("guidelines_mode", mode="before")
    @classmethod
    def coerce_invalid_mode(cls, v: str) -> str:
        if v not in ("regular", "consistency", "both"):
            logger.warning(f"Unrecognised EVOLVE_GUIDELINES_MODE value '{v}', defaulting to 'regular'")
            return "regular"
        return v


# to reload settings call guidelines_settings.__init__()
guidelines_settings = GuidelinesSettings()
