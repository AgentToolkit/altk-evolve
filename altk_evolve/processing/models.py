"""Transport-neutral processing definitions and immutable execution plans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from altk_evolve.schema.core import Entity


class ProcessingError(ValueError):
    """An invalid definition, unavailable processor, or failed profile lookup."""


class ProfileConflict(ProcessingError):
    """A profile changed since the caller read it."""


class ProfileNotFound(ProcessingError):
    """The requested profile/revision does not exist."""


class ProcessorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    plugin: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)


class ProfileDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=1)
    processors: list[ProcessorSpec]

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [p.id for p in self.processors]
        if len(set(ids)) != len(ids):
            raise ValueError("Processor instance IDs must be unique")
        return self


class ProfileReference(BaseModel):
    """No revision means follow latest at each trajectory boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    revision: int | None = Field(default=None, ge=1)


class Trajectory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    trace_id: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ProcessorContext:
    operation_id: str

    def complete(self, *, messages: list[dict], model: str, **kwargs):
        """LLM service preserving the deployment's egress hooks."""
        from litellm import completion
        from altk_evolve.hooks.manager import dispatch_llm_pre_call

        messages = dispatch_llm_pre_call(messages, purpose="trajectory_processor", model=model)
        return completion(messages=messages, model=model, **kwargs)


class ProcessorResult(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    enable_conflict_resolution: bool = False


class Processor(Protocol):
    """A plugin owns construction from validated config and execution on that instance."""

    id: ClassVar[str]
    api_version: ClassVar[int]
    version: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]

    @classmethod
    def from_config(cls, config: BaseModel) -> Self: ...

    def process(self, trajectory: Trajectory, *, context: ProcessorContext) -> ProcessorResult: ...


@dataclass(frozen=True)
class ProcessingPlan:
    """Execution snapshot: captured plugin classes plus one serialized configuration manifest.

    Classes correspond positionally to the manifest's ordered processor entries.
    The runner validates a fresh config and calls each class's from_config for every
    trajectory. No running instances or separately duplicated config records are kept.
    """

    processor_types: tuple[type[Processor], ...]
    manifest_json: str
    conflict_settings_json: str
    profile_id: str | None = None
    revision: int | None = None

    def manifest(self) -> dict:
        return cast(dict, json.loads(self.manifest_json))


class ProcessingResult(BaseModel):
    operation_id: str
    manifest: dict[str, Any]
    entities: list[Entity]
    updates: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
