"""A fake dataset adapter, an echo processor and a scripted guideline processor: no LLM, no network."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.processing import ProcessorContext, ProcessorResult, Trajectory, TrajectoryBatch, TrajectoryOutcome
from altk_evolve.processing.builtin import GuidelineProcessor
from altk_evolve.schema.core import Entity
from altk_evolve.schema.guidelines import Guideline, GuidelineGenerationResult

from experiments.guideline_pipeline.adapters.base import AdapterRecord


class EchoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = "default"
    fail_on: str | None = None


class EchoProcessor:
    """One note per trajectory, echoing what the adapter supplied."""

    id: ClassVar[str] = "tests.pipeline_echo"
    api_version: ClassVar[int] = 1
    version: ClassVar[str] = "1"
    config_model: ClassVar[type[BaseModel]] = EchoConfig

    def __init__(self, config: EchoConfig):
        self.config = config

    @classmethod
    def from_config(cls, config: BaseModel) -> Self:
        return cls(EchoConfig.model_validate(config))

    def process(self, trajectory: Trajectory, *, context: ProcessorContext) -> ProcessorResult:
        if trajectory.trace_id == self.config.fail_on:
            raise RuntimeError("processor failed")
        outcome = trajectory.outcome
        metadata = {"label": self.config.label, "success": outcome.success if outcome else None, "model": trajectory.model}
        return ProcessorResult(
            entities=[Entity(type="note", content=f"{trajectory.trace_id}: {trajectory.messages[-1]['content']}", metadata=metadata)]
        )


def _scripted_guidelines(trajectory: Trajectory) -> list[GuidelineGenerationResult]:
    """One guideline per trajectory; task_description is the first user message, as with segmentation off."""
    instruction = next(m["content"] for m in trajectory.messages if m["role"] == "user")
    guideline = Guideline(
        content=f"For tasks like '{instruction}', check the answer '{trajectory.messages[-1]['content']}'.",
        rationale="scripted",
        category="strategy",
        trigger="always",
    )
    return [GuidelineGenerationResult(guidelines=[guideline], task_description=instruction)]


class ScriptedGuidelineProcessor(GuidelineProcessor):
    """The built-in evolve.guidelines processor with the LLM step scripted.

    Its entity metadata (source_task_id, task_description, support, evidence) is
    built by the library's own process(); only generation is replaced, and
    conflict resolution is turned off because it is another LLM call.
    """

    id: ClassVar[str] = "tests.pipeline_guidelines"
    config_model: ClassVar[type[BaseModel]] = EchoConfig

    @classmethod
    def from_config(cls, config: BaseModel) -> Self:
        return cls((("standard", _scripted_guidelines),))

    def process(self, trajectory: Trajectory, *, context: ProcessorContext) -> ProcessorResult:
        return super().process(trajectory, context=context).model_copy(update={"enable_conflict_resolution": False})


class FakeAdapter:
    """Reads a JSON list of {"task", "answer", "success", "revision"?} objects."""

    name = "fake"

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        for item in json.loads(path.read_text()):
            yield AdapterRecord(
                messages=[{"role": "user", "content": f"do {item['task']}"}, {"role": "assistant", "content": item["answer"]}],
                trace_id=item["task"],
                model="fake-model",
                batch=TrajectoryBatch(
                    source="fake", conversation_id=item["task"], batch_id="attempt-1", revision=item.get("revision", "1")
                ),
                outcome=TrajectoryOutcome(success=item["success"]),
            )


def profile(label: str = "v1", fail_on: str | None = None) -> dict:
    config: dict = {"label": label} if fail_on is None else {"label": label, "fail_on": fail_on}
    return {"processors": [{"id": "echo", "plugin": EchoProcessor.id, "config": config}]}


def guideline_profile() -> dict:
    return {"processors": [{"id": "guidelines", "plugin": ScriptedGuidelineProcessor.id, "config": {}}]}


def store_guidelines(client: EvolveClient, namespace_id: str, *guidelines: tuple[str, dict]) -> None:
    """Write (content, metadata) guidelines directly, as consolidation does: no LLM, no conflict resolution."""
    client.ensure_namespace(namespace_id)
    entities = [Entity(type="guideline", content=content, metadata=metadata) for content, metadata in guidelines]
    client.update_entities(namespace_id, entities, enable_conflict_resolution=False)
