from dataclasses import dataclass
from pydantic import BaseModel, Field, model_validator
from typing import Literal

DEFAULT_TASK_DESCRIPTION = "Task description unknown"


Evidence = Literal["success", "failure", "both"]


class Guideline(BaseModel):
    content: str = Field(description="Clear, actionable guideline")
    rationale: str = Field(description="Why this guideline helps")
    category: Literal["strategy", "recovery", "optimization"]
    trigger: str = Field(description="When to apply this guideline")
    implementation_steps: list[str] = Field(default_factory=list, description="Specific steps to implement this guideline")
    support: int = Field(
        default=1,
        ge=1,
        description="Number of source guidelines merged into this one. Conserved across consolidation (dosage signal).",
    )
    evidence: Evidence | None = Field(
        default=None,
        description="Whether the backing trajectories succeeded, failed, or both. None when unknown.",
    )


class GuidelineGenerationResponse(BaseModel):
    guidelines: list[Guideline]


class ConsolidatedGuideline(Guideline):
    """A consolidated guideline that records which input guidelines it subsumes.

    ``source_indices`` are 0-based indices into the list of input guidelines shown to
    the model. They let consolidation attribute (and conserve) support exactly, rather
    than trusting the model to report counts.
    """

    source_indices: list[int] = Field(
        default_factory=list,
        description="0-based indices of the input guidelines this consolidated guideline merges.",
    )


class ConsolidatedGuidelineResponse(BaseModel):
    guidelines: list[ConsolidatedGuideline]


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
    """Summary of a guideline consolidation run.

    ``support_before``/``support_after`` track the total support (sum of ``support`` over
    the affected guidelines) before and after consolidation. For the current lossless/lossy
    modes they are equal (support is conserved). A future support-threshold filtering step
    (not yet implemented) may reduce ``support_after`` by pruning low-support guidelines.
    """

    clusters_found: int
    guidelines_before: int
    guidelines_after: int
    support_before: int = 0
    support_after: int = 0
