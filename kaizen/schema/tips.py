from dataclasses import dataclass
from pydantic import BaseModel, Field
from typing import Literal

DEFAULT_TASK_DESCRIPTION = "Task description unknown"


class Tip(BaseModel):
    content: str = Field(description="Clear, actionable tip")
    rationale: str = Field(description="Why this tip helps")
    category: Literal["strategy", "recovery", "optimization"]
    trigger: str = Field(description="When to apply this tip")


class TipGenerationResponse(BaseModel):
    tips: list[Tip]


@dataclass(frozen=True)
class TipGenerationResult:
    """Internal result from generate_tips(), pairing tips with the source task description."""

    tips: list[Tip]
    task_description: str


@dataclass(frozen=True)
class ConsolidationResult:
    """Summary of a tip consolidation run."""

    clusters_found: int
    tips_before: int
    tips_after: int
