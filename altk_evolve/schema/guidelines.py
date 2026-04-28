from dataclasses import dataclass
from pydantic import BaseModel, Field, model_validator
from typing import Literal

DEFAULT_TASK_DESCRIPTION = "Task description unknown"


class Guideline(BaseModel):
    content: str = Field(description="Clear, actionable guideline")
    rationale: str = Field(description="Why this guideline helps")
    category: Literal["strategy", "recovery", "optimization"]
    trigger: str = Field(description="When to apply this guideline")
    implementation_steps: list[str] = Field(default_factory=list, description="Specific steps to implement this guideline")


class GuidelineGenerationResponse(BaseModel):
    guidelines: list[Guideline]


class SubtaskSegment(BaseModel):
    generalized_description: str = Field(
        description="Value-agnostic description of the subtask, applicable to any agent performing a similar operation"
    )
    start_step: int = Field(
        ge=1,
        description=(
            "Inclusive 1-based start index into the filtered reasoning+action steps_list "
            "returned by parse_openai_agents_trajectory — NOT an index into raw messages."
        ),
    )
    end_step: int = Field(
        ge=1,
        description=(
            "Inclusive 1-based end index into the filtered reasoning+action steps_list "
            "returned by parse_openai_agents_trajectory — NOT an index into raw messages."
        ),
    )
    purpose: str = Field(description="What this subtask achieves (phase/output-oriented)")

    @model_validator(mode="after")
    def _check_range(self) -> "SubtaskSegment":
        if self.end_step < self.start_step:
            raise ValueError("end_step must be >= start_step")
        return self


class SegmentationResponse(BaseModel):
    subtasks: list[SubtaskSegment] = Field(description="Contiguous, non-overlapping logical subtasks of the trajectory")


@dataclass(frozen=True)
class GuidelineGenerationResult:
    """Internal result from generate_guidelines(), pairing guidelines with the source task description."""

    guidelines: list[Guideline]
    task_description: str


@dataclass(frozen=True)
class ConsolidationResult:
    """Summary of a guideline consolidation run."""

    clusters_found: int
    guidelines_before: int
    guidelines_after: int
