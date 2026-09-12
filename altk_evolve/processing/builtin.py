"""First-party processors use exactly the public processor contract."""

from __future__ import annotations

from altk_evolve import __version__
from pathlib import Path
from typing import ClassVar, Literal, Self, cast

from pydantic import BaseModel, Field, model_validator

from altk_evolve.config.guideline_runtime import GuidelineRuntime
from altk_evolve.processing.models import ProcessorContext, ProcessorResult, Trajectory
from altk_evolve.schema.core import Entity


def _analysis_defaults() -> dict:
    import yaml

    path = Path(__file__).parents[1] / "llm/guidelines/consistency_analyzer/agent_config.yaml"
    return cast(dict, yaml.safe_load(path.read_text()))


class GuidelineConfig(GuidelineRuntime):
    guidelines_mode: Literal["standard", "consistency", "all"] = "standard"
    consistency_method: Literal["fast", "accurate"] = "fast"
    guidelines_model: str = Field(default_factory=lambda: GuidelineRuntime.legacy().guidelines_model)
    custom_llm_provider: str | None = Field(default_factory=lambda: GuidelineRuntime.legacy().custom_llm_provider)
    segmentation_enabled: bool = Field(default_factory=lambda: GuidelineRuntime.legacy().segmentation_enabled)

    @model_validator(mode="after")
    def capture_analysis(self):
        if self.guidelines_mode != "standard" and self.consistency_method == "accurate":
            data = _analysis_defaults() if self.analysis_config is None else dict(self.analysis_config)
            if data.get("low_uncertainty_threshold", 0.1) > data.get("high_uncertainty_threshold", 0.5):
                raise ValueError("low_uncertainty_threshold must not exceed high_uncertainty_threshold")
            if data.get("max_samples", 10) < 1:
                raise ValueError("max_samples must be positive")
            object.__setattr__(self, "analysis_config", data)
        return self


class GuidelineProcessor:
    id: ClassVar[str] = "evolve.guidelines"
    api_version: ClassVar[int] = 1
    version: ClassVar[str] = __version__
    config_model: ClassVar[type[BaseModel]] = GuidelineConfig

    def __init__(self, config: GuidelineConfig):
        self.config = config

    @classmethod
    def from_config(cls, config: BaseModel) -> Self:
        return cls(GuidelineConfig.model_validate(config))

    def process(self, trajectory: Trajectory, *, context: ProcessorContext) -> ProcessorResult:
        config = self.config
        from altk_evolve.llm.guidelines.guidelines import generate_guidelines
        from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines, generate_consistency_guidelines_fast

        options = GuidelineRuntime.model_validate(config.model_dump(include=set(GuidelineRuntime.model_fields)))
        batches = []
        if config.guidelines_mode in ("standard", "all"):
            batches.append(("standard", generate_guidelines(trajectory.messages, options=options)))
        if config.guidelines_mode in ("consistency", "all"):
            if config.consistency_method == "fast":
                batches.append(("consistency-fast", generate_consistency_guidelines_fast(trajectory.model_dump(), options=options)))
            else:
                batches.append(("consistency", generate_consistency_guidelines(trajectory.model_dump(), options=options)))
        entities = [
            Entity(
                type="guideline",
                content=guideline.content,
                metadata={
                    **trajectory.metadata,
                    "source_task_id": trajectory.trace_id or context.operation_id,
                    "task_description": result.task_description,
                    "support": 1,
                    **guideline.model_dump(exclude={"content"}),
                    "generation_method": method,
                },
            )
            for method, results in batches
            for result in results
            for guideline in result.guidelines
        ]
        return ProcessorResult(entities=entities, enable_conflict_resolution=True)
