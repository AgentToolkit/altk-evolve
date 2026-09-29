"""The backend capabilities available to memory hooks.

Hooks transform/veto their payload. They may read entities and request metadata
patches, but do not own connections, transactions, namespaces, or entity batches.
Inside a processing transaction these methods operate on its private working
copy; patches become durable only when that operation commits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from altk_evolve.backend.base import BaseEntityBackend
    from altk_evolve.schema.core import Namespace, RecordedEntity


class HookBackend:
    """Read access and metadata patches, without exposing storage internals."""

    def __init__(self, backend: BaseEntityBackend):
        self.__backend = backend

    def search_entities(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        return self.__backend.search_entities(namespace_id, query, filters, limit)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        return self.__backend.get_namespace_details(namespace_id)

    def update_entity_metadata(self, namespace_id: str, entity_id: str, metadata_patch: dict) -> RecordedEntity:
        return self.__backend.update_entity_metadata(namespace_id, entity_id, metadata_patch)
