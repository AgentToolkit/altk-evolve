"""Explicit generation settings shared by built-in guideline implementations."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict


class GuidelineRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    guidelines_model: str = "gpt-4o"
    custom_llm_provider: str | None = None
    segmentation_enabled: bool = True
    analysis_config: dict[str, Any] | None = None

    @classmethod
    def legacy(cls):
        from altk_evolve.config.evolve import evolve_config
        from altk_evolve.config.llm import llm_settings

        return cls(
            guidelines_model=llm_settings.guidelines_model,
            custom_llm_provider=llm_settings.custom_llm_provider,
            segmentation_enabled=evolve_config.segmentation_enabled,
        )
