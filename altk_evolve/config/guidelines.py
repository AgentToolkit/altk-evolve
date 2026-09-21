import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self

logger = logging.getLogger(__name__)

# Replaced by agent_config.yaml's high_uncertainty_threshold / low_uncertainty_threshold /
# skip_on_no_uncertainty (accurate-method-only knobs). extra="ignore" below means a
# deployment that still sets these silently loses them with no error — warn instead.
_REMOVED_UNCERTAINTY_ENV_VARS = (
    "EVOLVE_HIGH_UNCERTAINTY_THRESHOLD",
    "EVOLVE_LOW_UNCERTAINTY_THRESHOLD",
    "EVOLVE_SKIP_ON_NO_UNCERTAINTY",
)


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

    @model_validator(mode="after")
    def warn_on_removed_uncertainty_env_vars(self) -> Self:
        stale = [name for name in _REMOVED_UNCERTAINTY_ENV_VARS if os.getenv(name) is not None]
        if stale:
            logger.warning(
                f"{', '.join(stale)} are no longer read (extra='ignore' silently drops them) — "
                "the accurate consistency method's uncertainty tuning now lives in "
                "agent_config.yaml (high_uncertainty_threshold / low_uncertainty_threshold / "
                "skip_on_no_uncertainty), passed via generate_consistency_guidelines(config_path=...)."
            )
        return self


# to reload settings call guidelines_settings.__init__()
guidelines_settings = GuidelinesSettings()
