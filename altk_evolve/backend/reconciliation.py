"""Pure reconciliation rules: inputs are snapshots; outputs are independent values.

The caller supplies the observation time so source bookkeeping never reads a clock.
Database reads, model calls, and policy hooks belong to the backend orchestration.
"""

from copy import deepcopy
from datetime import datetime

from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity, RecordedEntity
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.schema.provenance import identity, attach_sources, sources


def group_incoming(entities: list[Entity], observed_at: datetime) -> list[list[RecordedEntity]]:
    groups: dict[tuple, list[RecordedEntity]] = {}
    for index, entity in enumerate(entities):
        data = entity.model_dump()
        data["metadata"] = data.get("metadata") or {}
        incoming = RecordedEntity(**data, created_at=observed_at, id=f"Unprocessed_Entity_{index}")
        groups.setdefault(identity(incoming), []).append(incoming)
    return list(groups.values())


def new_memory_metadata(metadata: dict, contributors: list[Entity], observed_at: datetime) -> dict:
    result = attach_sources(metadata, None, contributors, associated_at=observed_at)
    if result.get("sources"):
        result["memory_revision"] = 1
    return result


def existing_memory_metadata(
    decision: EntityUpdate, old: RecordedEntity, contributors: list[Entity], unmatched_sources: bool, observed_at: datetime
) -> dict:
    """Preserve stored identity/holds while combining evidence and content revisions."""
    metadata = {**old.metadata, **decision.metadata}
    if len(contributors) == 1:
        metadata.update(contributors[0].metadata)
    if "generation_method" in old.metadata:
        metadata["generation_method"] = old.metadata["generation_method"]
    for key in ("user_id", "owner_id", "agent_id", "visibility"):
        metadata.pop(key, None)
        if key in old.metadata:
            metadata[key] = old.metadata[key]
    metadata = attach_sources(metadata, old, contributors, supersedes=decision.supersedes and bool(contributors), associated_at=observed_at)
    if metadata.get("sources"):
        metadata["memory_revision"] = int(old.metadata.get("memory_revision", 1)) + (decision.event == "UPDATE")
    # Missing associations from legacy/custom prompts cannot authorize retention.
    if (decision.event == "UPDATE" and not contributors) or unmatched_sources:
        metadata["provenance_incomplete"] = True
    if old.metadata.get("legal_hold"):
        metadata["legal_hold"] = deepcopy(old.metadata["legal_hold"])
    return metadata


def reconcile_decisions(
    incoming: list[RecordedEntity],
    candidates: dict[str, RecordedEntity],
    decisions: list[EntityUpdate],
    entity_type: str,
    observed_at: datetime,
) -> list[EntityUpdate]:
    """Validate model references and return changes with authoritative provenance."""
    incoming_by_id = {entity.id: entity for entity in incoming}
    associated = {identifier for decision in decisions for identifier in decision.incoming_ids}
    associated.update(
        decision.id for decision in decisions if decision.event == "ADD" or (decision.event == "NONE" and decision.id in incoming_by_id)
    )
    unmatched_sources = any(sources(entity) and entity.id not in associated for entity in incoming)
    changes = []
    for decision in decisions:
        if decision.type != entity_type:
            raise EvolveException("Conflict resolution changed entity type")
        if any(identifier not in incoming_by_id for identifier in decision.incoming_ids):
            raise EvolveException("Conflict resolution returned an unknown source ID")
        change = decision.model_copy(deep=True)
        if decision.event == "ADD":
            if decision.id not in incoming_by_id:
                raise EvolveException("Conflict resolution returned an unknown incoming ID")
            contributors = [incoming_by_id[identifier] for identifier in dict.fromkeys([decision.id, *decision.incoming_ids])]
            change.metadata = new_memory_metadata(incoming_by_id[decision.id].metadata, list(contributors), observed_at)
        else:
            if decision.event == "NONE" and decision.id in incoming_by_id and not decision.incoming_ids:
                continue
            if decision.id not in candidates:
                raise EvolveException("Conflict resolution returned an out-of-scope entity ID")
            old = candidates[decision.id]
            contributors = [incoming_by_id[identifier] for identifier in dict.fromkeys(decision.incoming_ids)]
            change.metadata = existing_memory_metadata(decision, old, list(contributors), unmatched_sources, observed_at)
            if decision.event == "NONE":
                change.content = deepcopy(old.content)
        changes.append(change)
    return changes


def attach_processing_provenance(
    changes: list[EntityUpdate],
    stored: dict[str, RecordedEntity],
    provenance: dict | None,
) -> list[EntityUpdate]:
    result = deepcopy(changes)
    if provenance is None:
        return result
    for change in result:
        if change.event not in ("ADD", "UPDATE"):
            continue
        previous = stored.get(change.id) if change.event == "UPDATE" else None
        if previous is not None:
            history = deepcopy(previous.metadata.get("processing_history", []))
            prior = previous.metadata.get("processing")
            if prior is not None:
                history.append(deepcopy(prior))
            if history:
                change.metadata["processing_history"] = history
        change.metadata["processing"] = deepcopy(provenance)
    return result


def prepare_additions(entities: list[Entity], entity_type: str, observed_at: datetime) -> list[EntityUpdate]:
    return [
        EntityUpdate(
            id="",
            type=entity_type,
            content=deepcopy(entity.content),
            event="ADD",
            metadata=new_memory_metadata(entity.metadata or {}, [entity], observed_at),
        )
        for entity in entities
    ]
