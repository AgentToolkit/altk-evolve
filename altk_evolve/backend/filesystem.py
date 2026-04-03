import datetime
import json
import logging
import uuid
from pathlib import Path
from threading import Lock

from pydantic import Field

from altk_evolve.backend.base import BaseEntityBackend
from altk_evolve.config.filesystem import FilesystemSettings, filesystem_settings
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity, Namespace, RecordedEntity
from altk_evolve.schema.exceptions import (
    EvolveException,
    NamespaceAlreadyExistsException,
    NamespaceNotFoundException,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("entities-db.filesystem")


class FilesystemNamespace(Namespace):
    """Extended Namespace with additional fields for filesystem storage."""

    entities: list[dict] = Field(default_factory=list, description="List of entity dictionaries")
    next_id: int = Field(default=1, description="Next available entity ID")


class FilesystemEntityBackend(BaseEntityBackend):
    """A filesystem-based backend that stores data in JSON files.

    This backend uses simple text matching for search (no embeddings).
    """

    def __init__(self, config: FilesystemSettings | None = None):
        self.config = config or filesystem_settings
        self.data_dir = Path(self.config.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        # Holds the loaded namespace data during update_entities so hooks can access it.
        self._active_data: FilesystemNamespace | None = None

    def _namespace_file(self, namespace_id: str) -> Path:
        """Get the path to a namespace's JSON file."""
        return self.data_dir / f"{namespace_id}.json"

    def _load_namespace_data(self, namespace_id: str) -> FilesystemNamespace:
        """Load namespace data from JSON file."""
        file_path = self._namespace_file(namespace_id)
        if not file_path.exists():
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")
        return FilesystemNamespace.model_validate(json.loads(file_path.read_text()))

    def _save_namespace_data(self, namespace_id: str, data: FilesystemNamespace):
        """Save namespace data to JSON file."""
        file_path = self._namespace_file(namespace_id)
        file_path.write_text(data.model_dump_json(indent=2))

    def ready(self) -> bool:
        """Check if the backend is healthy."""
        return True

    def details(self) -> dict:
        """Return details about the backend."""
        return {"data_dir": str(self.data_dir)}

    def _validate_namespace(self, namespace_id: str) -> None:
        file_path = self._namespace_file(namespace_id)
        if not file_path.exists():
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        """Create a new namespace for entities to exist in."""
        namespace_id = namespace_id or "ns_" + str(uuid.uuid4()).replace("-", "_")
        file_path = self._namespace_file(namespace_id)

        with self._lock:
            if file_path.exists():
                raise NamespaceAlreadyExistsException(f'Namespace "{namespace_id}" already exists.')

            now = datetime.datetime.now(datetime.UTC)
            data = FilesystemNamespace(
                id=namespace_id,
                created_at=now,
                entities=[],
                next_id=1,
                num_entities=0,
            )
            self._save_namespace_data(namespace_id, data)

        return Namespace(id=namespace_id, created_at=now, num_entities=0)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        """Get details about a specific namespace."""
        with self._lock:
            data = self._load_namespace_data(namespace_id)
            return Namespace(
                id=data.id,
                created_at=data.created_at,
                num_entities=len(data.entities),
            )

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        """Search for namespaces."""
        namespaces = []
        with self._lock:
            for file_path in self.data_dir.glob("*.json"):
                try:
                    data = json.loads(file_path.read_text())
                    namespaces.append(
                        Namespace(
                            id=data["id"],
                            created_at=datetime.datetime.fromisoformat(data["created_at"]),
                            num_entities=len(data["entities"]),
                        )
                    )
                except (json.JSONDecodeError, KeyError):
                    continue
                if len(namespaces) >= limit:
                    break
        return namespaces

    def delete_namespace(self, namespace_id: str):
        """Delete a namespace and all its entities."""
        file_path = self._namespace_file(namespace_id)
        with self._lock:
            if not file_path.exists():
                return  # Already deleted, no-op
            file_path.unlink()

    # ── update_entities hooks ────────────────────────────────────────

    def _add_entity(self, namespace_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> str:
        assert self._active_data is not None
        entity_id = str(self._active_data.next_id)
        self._active_data.next_id += 1
        created_at_iso = datetime.datetime.fromtimestamp(timestamp, datetime.UTC).isoformat()
        self._active_data.entities.append(
            {
                "id": entity_id,
                "type": entity_type,
                "content": content_str,
                "created_at": created_at_iso,
                "metadata": metadata,
            }
        )
        return entity_id

    def _update_entity(self, namespace_id: str, entity_id: str, entity_type: str, content_str: str, timestamp: int, metadata: dict) -> None:
        assert self._active_data is not None
        created_at_iso = datetime.datetime.fromtimestamp(timestamp, datetime.UTC).isoformat()
        for ent in self._active_data.entities:
            if ent["id"] == entity_id:
                ent["content"] = content_str
                ent["created_at"] = created_at_iso
                ent["metadata"] = metadata
                break

    def _delete_entity(self, namespace_id: str, entity_id: str) -> None:
        assert self._active_data is not None
        self._active_data.entities = [e for e in self._active_data.entities if e["id"] != entity_id]

    def _post_update(self, namespace_id: str) -> None:
        assert self._active_data is not None
        self._active_data.num_entities = len(self._active_data.entities)
        self._save_namespace_data(namespace_id, self._active_data)
        self._active_data = None

    def update_entities(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True,
    ) -> list[EntityUpdate]:
        """Override to wrap the base template in a lock with loaded data."""
        with self._lock:
            self._active_data = self._load_namespace_data(namespace_id)
            return super().update_entities(namespace_id, entities, enable_conflict_resolution)

    # ── search ───────────────────────────────────────────────────────

    def _search_entities_internal(
        self,
        data: FilesystemNamespace,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        """Internal search method that works on loaded data."""
        entities = data.entities
        filters = filters or {}

        # Apply filters
        if filters:
            filtered = []
            for ent in entities:
                match = True
                for key, value in filters.items():
                    if key.startswith("metadata."):
                        metadata_key = key.split(".", 1)[1]
                        ent_value = (ent.get("metadata") or {}).get(metadata_key)
                    else:
                        # Check top-level field first, then metadata
                        ent_value = ent.get(key)
                        if ent_value is None and ent.get("metadata"):
                            ent_value = ent["metadata"].get(key)
                    if ent_value != value:
                        match = False
                        break
                if match:
                    filtered.append(ent)
            entities = filtered

        if query is None:
            # Return all entities (up to limit)
            results = entities[:limit]
        else:
            # Simple case-insensitive text matching
            query_lower = query.lower()
            matching = []
            for ent in entities:
                content = ent.get("content", "")
                # Convert non-string content to JSON string for searching
                if not isinstance(content, str):
                    content = json.dumps(content)
                if query_lower in content.lower():
                    matching.append(ent)
            results = matching[:limit]

        return [
            RecordedEntity(
                id=str(ent["id"]),
                type=ent["type"],
                content=ent["content"],
                created_at=datetime.datetime.fromisoformat(ent["created_at"]),
                metadata=ent.get("metadata") or {},
            )
            for ent in results
        ]

    def search_entities(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        """Search for entities in a namespace."""
        # If called during update_entities (inside the lock), use the active data
        if self._active_data is not None:
            return self._search_entities_internal(self._active_data, query, filters, limit)
        with self._lock:
            data = self._load_namespace_data(namespace_id)
            return self._search_entities_internal(data, query, filters, limit)

    def delete_entity_by_id(self, namespace_id: str, entity_id: str):
        """Delete a specific entity by its ID."""
        with self._lock:
            data = self._load_namespace_data(namespace_id)
            original_count = len(data.entities)
            data.entities = [e for e in data.entities if str(e["id"]) != entity_id]
            if len(data.entities) == original_count:
                raise EvolveException(f"Entity `{entity_id}` not found")
            data.num_entities = len(data.entities)
            self._save_namespace_data(namespace_id, data)
