"""First-party processors use exactly the public processor contract."""

from __future__ import annotations

from pathlib import Path
import json
from collections.abc import Callable
from typing import ClassVar, Literal, Self, cast

from pydantic import BaseModel, Field, model_validator

from altk_evolve.config.guideline_runtime import GuidelineRuntime
from altk_evolve.processing.models import ProcessorContext, ProcessorResult, Trajectory
from altk_evolve.schema.core import Entity
from altk_evolve.schema.guidelines import GuidelineGenerationResult


def _analysis_defaults() -> dict:
    import yaml

    path = Path(__file__).parents[1] / "llm/guidelines/consistency_analyzer/agent_config.yaml"
    return cast(dict, yaml.safe_load(path.read_text()))


def _generation_view(trajectory: Trajectory) -> Trajectory:
    """Render history for guideline extraction only; never use this view for resampling."""
    if trajectory.context_messages:
        task_context = {
            "role": "user",
            "content": "The following is supporting conversation context, not new steps to learn from. "
            "Derive guidelines only from the new steps that follow, using this context to interpret them.\n"
            + json.dumps(trajectory.context_messages, ensure_ascii=False),
        }
        trajectory = trajectory.model_copy(update={"messages": [task_context, *trajectory.messages]})
    return trajectory


class GuidelineConfig(GuidelineRuntime):
    guidelines_mode: Literal["standard", "consistency", "all"] = "standard"
    consistency_method: Literal["fast", "accurate"] = "fast"
    guidelines_model: str = Field(default_factory=lambda: GuidelineRuntime.from_settings().guidelines_model)
    custom_llm_provider: str | None = Field(default_factory=lambda: GuidelineRuntime.from_settings().custom_llm_provider)
    segmentation_enabled: bool = Field(default_factory=lambda: GuidelineRuntime.from_settings().segmentation_enabled)

    @model_validator(mode="after")
    def capture_analysis(self):
        if self.guidelines_mode != "standard" and self.consistency_method == "accurate":
            data = {**_analysis_defaults(), **(self.analysis_config or {})}
            for key, limit in (("max_samples", 100), ("max_steps", 1000)):
                value = data[key]
                if type(value) is not int or not 1 <= value <= limit:
                    raise ValueError(f"{key} must be an integer between 1 and {limit}")
            threshold = data["high_uncertainty_threshold"]
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
                raise ValueError("high_uncertainty_threshold must be in (0, 1]")
            if data["aggregation"] not in ("mean", "rms", "geo_mean", "product"):
                raise ValueError("Unknown consistency aggregation")
            if type(data["skip_on_no_uncertainty"]) is not bool:
                raise ValueError("skip_on_no_uncertainty must be a boolean")
            if not isinstance(data["agents"], list) or not data["agents"]:
                raise ValueError("agents must be a nonempty list")
            for agent in data["agents"]:
                if not isinstance(agent, dict) or not agent.get("name") or not agent.get("response_type"):
                    raise ValueError("Each agent requires name and response_type")
                structured = agent["response_type"] in ("json", "react", "react_aw", "thought_code", "tool_calls")
                if agent["response_type"] in ("text", "code") or (structured and "fields" not in agent and "alternates" not in agent):
                    metric = agent.get("metric")
                    if not isinstance(metric, str) or not metric.strip():
                        raise ValueError(f"Agent {agent['name']} with response_type {agent['response_type']} requires a metric")
            object.__setattr__(self, "analysis_config", data)
        return self


class GuidelineProcessor:
    id: ClassVar[str] = "evolve.guidelines"
    api_version: ClassVar[int] = 1
    version: ClassVar[str] = "1"  # Bump for incompatible processor changes, independently of package releases.
    config_model: ClassVar[type[BaseModel]] = GuidelineConfig

    def __init__(self, steps: tuple[tuple[str, Callable[[Trajectory], list[GuidelineGenerationResult]]], ...]):
        self._steps = steps

    @classmethod
    def from_config(cls, config: BaseModel) -> Self:
        """Select generation steps once using this trajectory's resolved settings."""
        from altk_evolve.llm.guidelines.guidelines import generate_guidelines
        from altk_evolve.llm.guidelines.consistency_guidelines import generate_consistency_guidelines, generate_consistency_guidelines_fast

        config = GuidelineConfig.model_validate(config)
        options = GuidelineRuntime.model_validate(config.model_dump(include=set(GuidelineRuntime.model_fields)))
        steps: list[tuple[str, Callable[[Trajectory], list[GuidelineGenerationResult]]]] = []
        if config.guidelines_mode in ("standard", "all"):
            steps.append(("standard", lambda trajectory: generate_guidelines(_generation_view(trajectory).messages, options=options)))
        if config.guidelines_mode in ("consistency", "all"):
            if config.consistency_method == "fast":
                steps.append(
                    (
                        "consistency-fast",
                        lambda trajectory: generate_consistency_guidelines_fast(_generation_view(trajectory).model_dump(), options=options),
                    )
                )
            else:
                steps.append(("consistency", lambda trajectory: generate_consistency_guidelines(trajectory.model_dump(), options=options)))
        return cls(tuple(steps))

    def process(self, trajectory: Trajectory, *, context: ProcessorContext) -> ProcessorResult:
        """Run the selected generators on this bounded contribution."""
        batches = [(method, generate(trajectory)) for method, generate in self._steps]
        entities = [
            Entity(
                type="guideline",
                content=guideline.content,
                metadata={
                    **trajectory.metadata,
                    "source_task_id": trajectory.trace_id or context.operation_id,
                    "task_description": result.task_description,
                    "support": 1,
                    **guideline.model_dump(exclude={"content", "support", "evidence"}),
                    "generation_method": method,
                },
            )
            for method, results in batches
            for result in results
            for guideline in result.guidelines
        ]
        return ProcessorResult(entities=entities, enable_conflict_resolution=True)
