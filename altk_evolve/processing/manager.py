"""In-process profile management and trajectory execution shared by all interfaces."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import replace
from typing import Any

from altk_evolve.config.llm import LLMSettings
from altk_evolve.processing.models import (
    ProcessingError,
    ProcessingPlan,
    ProcessingResult,
    ProcessorContext,
    ProcessorResult,
    ProfileDefinition,
    ProfileReference,
    Trajectory,
)
from altk_evolve.processing.registry import ProcessorRegistry
from altk_evolve.processing.repository import InMemoryProfileRepository, ProfileRepository


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ProcessingManager:
    """Coordinate profile storage, plugin resolution, and execution in the caller’s process."""

    def __init__(self, *, registry: ProcessorRegistry | None = None, repository: ProfileRepository | None = None):
        self.registry = registry if registry is not None else ProcessorRegistry.discover()
        self.repository = repository if repository is not None else InMemoryProfileRepository()

    def validate(self, definition: ProfileDefinition | dict, *, conflict_settings: dict | None = None) -> ProcessingPlan:
        definition = ProfileDefinition.model_validate(definition)
        processors = []
        manifest: dict[str, Any] = {"schema_version": 1, "processors": []}
        for spec in definition.processors:
            descriptor = self.registry.get(spec.plugin)
            try:
                config = descriptor.config_model.model_validate(spec.config).model_dump(mode="json")
                _encode(config)  # Reject non-JSON/non-finite values before publication.
            except Exception as exc:
                raise ProcessingError(f"Invalid config for {spec.id} ({spec.plugin}): {exc}") from exc
            processors.append(descriptor)
            manifest["processors"].append(
                {
                    "id": spec.id,
                    "plugin": spec.plugin,
                    "version": descriptor.version,
                    "api_version": descriptor.api_version,
                    "config": config,
                }
            )
        settings = LLMSettings() if conflict_settings is None else LLMSettings(**conflict_settings)
        conflict = {"conflict_resolution_model": settings.conflict_resolution_model, "custom_llm_provider": settings.custom_llm_provider}
        manifest["conflict_resolution"] = conflict
        return ProcessingPlan(tuple(processors), _encode(manifest), _encode(conflict))

    def default_plan(self) -> ProcessingPlan:
        """Capture existing deployment defaults when no profile/selector is supplied."""
        from altk_evolve.config.guidelines import guidelines_settings

        return self.validate(
            {
                "processors": [
                    {
                        "id": "guidelines",
                        "plugin": "evolve.guidelines",
                        "config": {
                            "guidelines_mode": guidelines_settings.guidelines_mode,
                            "consistency_method": guidelines_settings.consistency_method,
                        },
                    }
                ]
            }
        )

    def put(self, name: str, definition: dict | ProfileDefinition, *, expected_revision: int) -> dict:
        if not name or expected_revision < 0:
            raise ProcessingError("Profile name is required; revision must be >= 0 (0 creates)")
        plan = self.validate(definition)
        # Persist normalized configuration/defaults and implementation versions, not live pointers.
        revision = self.repository.put(name, plan.manifest(), expected_revision=expected_revision)
        return {"id": name, "revision": revision, "manifest": plan.manifest()}

    def get(self, name: str, revision: int | None = None) -> dict:
        number, manifest = self.repository.get(name, revision)
        return {"id": name, "revision": number, "manifest": manifest}

    def resolve(self, reference: ProfileReference | str, *, revision: int | None = None) -> ProcessingPlan:
        if isinstance(reference, str):
            reference = ProfileReference(id=reference, revision=revision)
        record = self.get(reference.id, reference.revision)
        manifest = record["manifest"]
        for item in manifest["processors"]:
            descriptor = self.registry.get(item["plugin"])
            if descriptor.version != item["version"] or descriptor.api_version != item["api_version"]:
                raise ProcessingError(f"Processor version changed: {item['plugin']}; publish a new profile revision")
        plan = self.validate(
            {"processors": [{k: p[k] for k in ("id", "plugin", "config")} for p in manifest["processors"]]},
            conflict_settings=manifest["conflict_resolution"],
        )
        if plan.manifest() != manifest:
            raise ProcessingError("Stored profile no longer resolves identically; publish a new revision")
        return replace(plan, profile_id=reference.id, revision=record["revision"])

    def process(
        self, trajectory: Trajectory | dict, *, plan: ProcessingPlan, client: Any = None, namespace_id: str | None = None
    ) -> ProcessingResult:
        if client is not None and namespace_id is None:
            raise ProcessingError("namespace_id is required for persistence")
        trajectory = Trajectory.model_validate(trajectory)
        if client is not None:
            client.get_namespace_details(namespace_id)
        operation_id = str(uuid.uuid4())
        context = ProcessorContext(operation_id)
        manifest = plan.manifest()
        provenance = {
            "operation_id": operation_id,
            "profile_id": plan.profile_id,
            "revision": plan.revision,
            "digest": hashlib.sha256(plan.manifest_json.encode()).hexdigest(),
            "manifest": manifest,
        }
        batches = []
        diagnostics = {}
        entities = []
        for processor_type, spec in zip(plan.processor_types, manifest["processors"], strict=True):
            config = processor_type.config_model.model_validate_json(_encode(spec["config"]))
            processor = processor_type.from_config(config)
            result = ProcessorResult.model_validate(processor.process(trajectory.model_copy(deep=True), context=context))
            diagnostics[spec["id"]] = result.diagnostics
            stamp = {**provenance, "processor_id": spec["id"]}
            for entity in result.entities:
                entity.metadata = {**entity.metadata, "processing": stamp}
            entities.extend(result.entities)
            batches.append((result, stamp))
        updates: list[dict[str, Any]] = []
        if client is not None:
            # All processors complete before writes. Backends can still fail partway through persistence.
            for result, stamp in batches:
                groups = defaultdict(list)
                for entity in result.entities:
                    groups[entity.type].append(entity)
                for group in groups.values():
                    written = client.update_entities(
                        namespace_id,
                        group,
                        enable_conflict_resolution=result.enable_conflict_resolution,
                        conflict_settings=LLMSettings(**json.loads(plan.conflict_settings_json)),
                        processing_provenance=stamp,
                    )
                    updates.extend(item.model_dump(mode="json") for item in written)
        return ProcessingResult(operation_id=operation_id, manifest=manifest, entities=entities, updates=updates, diagnostics=diagnostics)
