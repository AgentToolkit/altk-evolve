"""Explicit generation settings shared by built-in guideline implementations."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class GuidelineRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    guidelines_model: str = "gpt-4o"
    custom_llm_provider: str | None = None
    segmentation_enabled: bool = False
    # Trajectory rendering limits — see EvolveConfig for what each one does. Carried here so
    # a caller passing an explicit runtime gets the same window on every path, including the
    # segmenter's own parse of the same messages; a mismatch between the two would move the
    # step indices segmentation returns.
    trajectory_max_steps: int = Field(default=50, ge=1)
    trajectory_max_step_chars: int = Field(default=2000, ge=1)
    trajectory_tail_steps: int = Field(default=0, ge=0)
    analysis_config: dict[str, Any] | None = None

    @classmethod
    def from_settings(cls):
        from altk_evolve.config.evolve import evolve_config
        from altk_evolve.config.llm import llm_settings

        return cls(
            guidelines_model=llm_settings.guidelines_model,
            custom_llm_provider=llm_settings.custom_llm_provider,
            segmentation_enabled=evolve_config.segmentation_enabled,
            trajectory_max_steps=evolve_config.trajectory_max_steps,
            trajectory_max_step_chars=evolve_config.trajectory_max_step_chars,
            trajectory_tail_steps=evolve_config.trajectory_tail_steps,
        )
