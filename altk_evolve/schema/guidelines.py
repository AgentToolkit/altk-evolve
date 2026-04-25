from dataclasses import dataclass
from pydantic import BaseModel, Field
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
