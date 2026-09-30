"""Prepared storage changes; model calls and policy hooks finish before commit."""

from __future__ import annotations

import hashlib
import json

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
    """Opaque prepared changes: callers may inspect, but must not construct or mutate them.

    The receipt binds checked policy decisions to their backend, namespace, and
    contents. This guards public commit use, not hostile Python code accessing
    private methods in the same process.
    """

    timestamp: int
    updates: list[EntityUpdate] = field(default_factory=list)
    expected: dict[str, RecordedEntity] = field(default_factory=dict)
    patches: list[MetadataPatch] = field(default_factory=list)
    storage: dict[str, Any] = field(default_factory=dict)
    _receipt: tuple[object, str, str] | None = field(default=None, init=False, repr=False)

    def _digest(self) -> str:
        payload = [
            self.timestamp,
            [u.model_dump(mode="json") for u in self.updates],
            {k: v.model_dump(mode="json") for k, v in self.expected.items()},
            [(p.namespace_id, p.entity_id, p.patch) for p in self.patches],
            self.storage,
        ]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def _authorize(self, backend: object, namespace_id: str) -> None:
        self._receipt = (backend, namespace_id, self._digest())

    def _assert_authorized(self, backend: object, namespace_id: str) -> None:
        from altk_evolve.schema.exceptions import EvolveException

        if self._receipt != (backend, namespace_id, self._digest()):
            raise EvolveException("Use unchanged writes returned by this backend's prepare_updates() for this namespace")
