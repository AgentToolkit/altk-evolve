"""Prepared storage changes; model calls and policy hooks finish before commit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import RecordedEntity


@dataclass
class MetadataPatch:
    namespace_id: str
    entity_id: str
    patch: dict


@dataclass
class PreparedWrites:
    """A batch's mutations and only the existing entities it would replace or delete."""

    timestamp: int
    updates: list[EntityUpdate] = field(default_factory=list)
    expected: dict[str, RecordedEntity] = field(default_factory=dict)
    patches: list[MetadataPatch] = field(default_factory=list)
    storage: dict[str, Any] = field(default_factory=dict)
