"""In-process profile management and trajectory execution shared by all interfaces."""

from __future__ import annotations

import hashlib
import json
import uuid
import math
from copy import deepcopy
from collections import defaultdict
from dataclasses import dataclass, replace
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
    TrajectoryBatch,
)
from altk_evolve.processing.registry import ProcessorRegistry
from altk_evolve.processing.repository import InMemoryProfileRepository, ProfileRepository


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reject_nonfinite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Configuration must not contain non-finite numbers")
    if isinstance(value, dict):
        for item in value.values():
            _reject_nonfinite(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _reject_nonfinite(item)


@dataclass
class GeneratedProcessing:
    """Processor proposals awaiting per-processor output/checkpoint commits."""

    result: ProcessingResult
    batches: list[tuple[ProcessorResult, dict]]
    conflict_settings_json: str
    batch: TrajectoryBatch | None = None


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
                _reject_nonfinite(spec.config)
                validated = descriptor.config_model.model_validate(spec.config)
                _reject_nonfinite(validated.model_dump())
                config = validated.model_dump(mode="json")
                encoded = _encode(config)
                restored = descriptor.config_model.model_validate(config).model_dump(mode="json")
                executable = descriptor.config_model.model_validate_json(encoded).model_dump(mode="json")
                if _encode(restored) != encoded or _encode(executable) != encoded:
                    raise ValueError("Configuration does not round-trip through JSON identically")
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
        ProfileReference(id=name, revision=revision)
        number, manifest = self.repository.get(name, revision)
        return {"id": name, "revision": number, "manifest": manifest}

    def resolve(self, reference: ProfileReference | str, *, revision: int | None = None) -> ProcessingPlan:
        if isinstance(reference, str):
            reference = ProfileReference(id=reference, revision=revision)
        elif revision is not None:
            if reference.revision is not None and reference.revision != revision:
                raise ProcessingError("Conflicting profile revision arguments")
            reference = ProfileReference(id=reference.id, revision=revision)
        record = self.get(reference.id, reference.revision)
        manifest = record["manifest"]
        try:
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
        except ProcessingError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ProcessingError(f"Invalid stored profile {reference.id}: {exc}") from exc
        return replace(plan, profile_id=reference.id, revision=record["revision"])

    def process(
        self, trajectory: Trajectory | dict, *, plan: ProcessingPlan, client: Any = None, namespace_id: str | None = None
    ) -> ProcessingResult:
        """Process one bounded input with a fixed plan and independent processor checkpoints.

        Generation, hooks, and reconciliation run outside storage transactions.
        Each processor commits its outputs and checkpoint together; completed
        processors are skipped on redelivery even after a profile update. New
        batches resolve the latest profile in EvolveClient.process_trajectory().
        """
        if client is not None and namespace_id is None:
            raise ProcessingError("namespace_id is required for persistence")
        trajectory = Trajectory.model_validate(trajectory)
        skipped = set()
        if client is not None:
            client.get_namespace_details(namespace_id)
            if client.backend.in_transaction:
                raise ProcessingError("Process trajectories outside storage transactions")
            if trajectory.batch is not None:
                if not client.backend.supports_atomic_writes:
                    raise NotImplementedError("Incremental processing requires atomic namespace writes")
                for spec in plan.manifest()["processors"]:
                    keys = trajectory.batch.checkpoint_keys(spec["id"])
                    if any(client.backend.get_processing_checkpoint(namespace_id, key) is not None for key in keys):
                        if len(keys) > 1:
                            client.backend.commit_prepared(namespace_id, [], checkpoint=(keys[0], {}), checkpoint_aliases=tuple(keys[1:]))
                        skipped.add(spec["id"])
        generated = self.generate(trajectory, plan=plan, skipped=skipped)
        if client is None:
            return generated.result
        assert namespace_id is not None
        return self.persist(generated, client=client, namespace_id=namespace_id)

    def generate(self, trajectory: Trajectory | dict, *, plan: ProcessingPlan, skipped: set[str] | None = None) -> GeneratedProcessing:
        """Execute plugins once without acquiring an entity-storage transaction."""
        trajectory = Trajectory.model_validate(trajectory)
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
        if trajectory.batch is not None:
            provenance["source_batch"] = trajectory.batch.model_dump()
        batches = []
        diagnostics = {} if plan.processor_types else {"processing": {"warning": "No processors configured; no entities will be generated"}}
        entities = []
        for processor_type, spec in zip(plan.processor_types, manifest["processors"], strict=True):
            if skipped and spec["id"] in skipped:
                continue
            config = processor_type.config_model.model_validate_json(_encode(spec["config"]))
            processor = processor_type.from_config(config)
            result = ProcessorResult.model_validate(processor.process(trajectory.model_copy(deep=True), context=context))
            diagnostics[spec["id"]] = result.diagnostics
            stamp = {**provenance, "processor_id": spec["id"]}
            for entity in result.entities:
                entity.metadata = {**entity.metadata, "processing": deepcopy(stamp)}
            entities.extend(result.entities)
            batches.append((result, stamp))
        return GeneratedProcessing(
            ProcessingResult(
                operation_id=operation_id,
                manifest=manifest,
                entities=entities,
                diagnostics=diagnostics,
                skipped_processors=sorted(skipped or []),
            ),
            batches,
            plan.conflict_settings_json,
            trajectory.batch,
        )

    def persist(self, generated: GeneratedProcessing, *, client: Any, namespace_id: str) -> ProcessingResult:
        """Prepare against available memory, then commit each processor's contribution.

        Unrelated writes never invalidate preparation. A changed replacement or
        deletion target aborts that commit, leaving its checkpoint absent for a
        later delivery. Already committed processors remain complete.
        """
        updates: list[dict[str, Any]] = []
        completed = []
        skipped = list(generated.result.skipped_processors)
        conflict_settings = LLMSettings(**json.loads(generated.conflict_settings_json))
        for result, stamp in deepcopy(generated.batches):
            processor_id = stamp["processor_id"]
            checkpoint = None
            aliases: tuple[str, ...] = ()
            if generated.batch is not None:
                keys = generated.batch.checkpoint_keys(processor_id)
                checkpoint = (keys[0], stamp)
                aliases = tuple(keys[1:])
                if any(client.backend.get_processing_checkpoint(namespace_id, key) is not None for key in keys):
                    client.backend.commit_prepared(namespace_id, [], checkpoint=checkpoint, checkpoint_aliases=aliases)
                    skipped.append(processor_id)
                    continue
            groups = defaultdict(list)
            for entity in result.entities:
                groups[entity.type].append(entity)
            prepared = [
                client.backend.prepare_updates(
                    namespace_id,
                    group,
                    enable_conflict_resolution=result.enable_conflict_resolution,
                    conflict_settings=conflict_settings,
                    processing_provenance=stamp,
                )
                for group in groups.values()
            ]
            written = client.backend.commit_prepared(namespace_id, prepared, checkpoint=checkpoint, checkpoint_aliases=aliases)
            if written is None:
                skipped.append(processor_id)
            else:
                completed.append(processor_id)
                updates.extend(item.model_dump(mode="json") for item in written)
        return generated.result.model_copy(
            deep=True,
            update={
                "updates": updates,
                "completed_processors": completed,
                "skipped_processors": skipped,
            },
        )
