"""Content-free source associations maintained alongside a memory's current value."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from collections.abc import Sequence

from altk_evolve.schema.core import Entity


def identity(entity: Entity) -> tuple[Any, Any, Any, Any]:
    metadata = entity.metadata or {}
    return tuple(metadata.get(key) for key in ("user_id", "owner_id", "agent_id", "visibility"))  # type: ignore[return-value]


def sources(entity: Entity) -> list[dict]:
    metadata = entity.metadata or {}
    if "sources" in metadata:
        value = metadata["sources"]
        return deepcopy(value) if isinstance(value, list) and all(isinstance(v, dict) for v in value) else []
    conversation = metadata.get("thread_id") or metadata.get("session_id")
    task = metadata.get("source_task_id") or (metadata.get("task_id") if entity.type == "trajectory" else None)
    if not conversation and not task:
        return []
    return [
        {
            "conversation_id": conversation,
            "task_id": task,
            "user_id": metadata.get("user_id"),
            "agent_id": metadata.get("agent_id"),
            "status": "supporting",
        }
    ]


def attach_sources(
    metadata: dict, old: Entity | None, incoming: Sequence[Entity], *, supersedes: bool = False, associated_at: datetime | None = None
) -> dict:
    result = deepcopy(metadata)
    if old is None and not any(sources(entity) for entity in incoming):
        return result
    # Each association ages independently of later content revisions. Legacy
    # associations conservatively start at the last known entity timestamp.
    observed_at = associated_at if associated_at is not None else datetime.now(UTC)
    now = observed_at.isoformat()
    associations = sources(old) if old is not None else []
    for association in associations:
        association.setdefault("associated_at", getattr(old, "created_at", observed_at).isoformat())
    complete = bool(associations) and not (old.metadata or {}).get("provenance_incomplete", False) if old is not None else True
    if supersedes:
        for association in associations:
            association["status"] = "superseded"
        complete = True
    for entity in incoming:
        additions = sources(entity)
        complete = complete and bool(additions) and not (entity.metadata or {}).get("provenance_incomplete", False)
        for source in additions:
            # A fresh observation is stamped now. An association that already carries a
            # valid earlier time (e.g. moved onto a consolidated memory) keeps it, so
            # deletion receipts recorded before the move still apply. Any other supplied
            # time (future, naive or unparseable) is re-stamped: trusting it could keep a
            # memory whose source was deleted from ever being collected.
            if not _is_prior_time(source.get("associated_at"), observed_at):
                source["associated_at"] = now
            key = _association_key(source)
            for previous in [v for v in associations if _association_key(v) == key]:
                associations.remove(previous)
                source.update(_combine_duplicate(previous, source))
            associations.append(source)
    result["sources"] = associations
    result["provenance_incomplete"] = not complete
    return result


def with_association_times(entities: Sequence[Entity]) -> list[Entity]:
    """Copies of `entities` whose source associations all carry `associated_at`.

    A legacy association with no time ages from its entity's `created_at`, the same
    rule `Collection.source_deletion_times` applies, so moving it onto another memory
    keeps it collectible by receipts recorded after that entity was written.
    """
    result = []
    for entity in entities:
        created_at = getattr(entity, "created_at", None)
        associations = sources(entity)
        if created_at is None or all("associated_at" in a for a in associations):
            result.append(entity)
            continue
        for association in associations:
            association.setdefault("associated_at", created_at.isoformat())
        result.append(entity.model_copy(update={"metadata": {**(entity.metadata or {}), "sources": associations}}))
    return result


def _is_prior_time(value: Any, ceiling: datetime) -> bool:
    """True if `value` is a timezone-aware ISO time no later than `ceiling`."""
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.tzinfo is not None and parsed <= ceiling
    except (TypeError, ValueError):
        return False


def _association_key(source: dict) -> tuple:
    return tuple(source.get(k) for k in ("conversation_id", "task_id", "user_id", "agent_id"))


def _combine_duplicate(previous: dict, source: dict) -> dict:
    """Two records of one association combine conservatively for retention.

    It stays supporting if either record was, and ages from the later time. For a fresh
    observation (stamped now, supporting) this is the same as letting it replace the old
    record; it only matters when both records were already stamped.
    """
    combined: dict = {}
    if previous.get("status") == "supporting":
        combined["status"] = "supporting"
    try:
        if datetime.fromisoformat(previous["associated_at"]) > datetime.fromisoformat(source["associated_at"]):
            combined["associated_at"] = previous["associated_at"]
    except (KeyError, TypeError, ValueError):
        pass
    return combined
