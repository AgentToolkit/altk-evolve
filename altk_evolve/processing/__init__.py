"""Pluggable trajectory processing, immutable plans, and optional versioned profiles."""

from altk_evolve.processing.models import (
    ProcessingError,
    ProcessingPlan,
    ProcessingResult,
    Processor,
    ProcessorContext,
    ProcessorResult,
    ProfileConflict,
    ProfileDefinition,
    ProfileNotFound,
    ProfileReference,
    Trajectory,
)
from altk_evolve.processing.registry import ProcessorRegistry
from altk_evolve.processing.repository import (
    InMemoryProfileRepository,
    PostgresProfileRepository,
    ProfileRepository,
    SQLiteProfileRepository,
)
from altk_evolve.processing.manager import ProcessingManager

__all__ = [
    "ProcessingError",
    "ProcessingPlan",
    "ProcessingResult",
    "Processor",
    "ProcessorContext",
    "ProcessorResult",
    "ProfileConflict",
    "ProfileDefinition",
    "ProfileNotFound",
    "ProfileReference",
    "Trajectory",
    "ProcessorRegistry",
    "InMemoryProfileRepository",
    "ProfileRepository",
    "SQLiteProfileRepository",
    "PostgresProfileRepository",
    "ProcessingManager",
]
