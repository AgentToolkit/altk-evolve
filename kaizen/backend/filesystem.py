import datetime
import json
import logging
import uuid
from pathlib import Path
from threading import Lock

from pydantic import Field

from kaizen.backend.base import BaseEntityBackend
from kaizen.config.filesystem import FilesystemSettings, filesystem_settings
from kaizen.llm.conflict_resolution.conflict_resolution import resolve_conflicts
from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.core import Entity, Namespace, RecordedEntity
from kaizen.schema.exceptions import (
    KaizenException,
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

    def update_entities(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True,
    ) -> list[EntityUpdate]:
        """Add/update entities in a namespace."""
        if len(entities) == 0:
            return []

        entity_type = entities[0].type
        if not all(entity.type == entity_type for entity in entities):
            raise KaizenException("All entities must have the same type.")

        now = datetime.datetime.now(datetime.UTC)
        now_iso = now.isoformat()

        # Create temporary entities with placeholder IDs
        entities_with_temporary_ids = []
        for i, entity in enumerate(entities):
            entity_data = entity.model_dump()
            if entity_data.get("metadata") is None:
                entity_data["metadata"] = {}
            entities_with_temporary_ids.append(
                RecordedEntity(
                    **entity_data,
                    created_at=now,
                    id=f"Unprocessed_Entity_{i}",
                )
            )

        with self._lock:
            data = self._load_namespace_data(namespace_id)

            if enable_conflict_resolution:
                # Find similar existing entities for conflict resolution
                old_entities = []
                for entity in entities:
                    # Convert content to string for search query
                    query_str = entity.content if isinstance(entity.content, str) else json.dumps(entity.content)
                    similar = self._search_entities_internal(data, query=query_str, filters=None, limit=10)
                    old_entities.extend(similar)

                updates = resolve_conflicts(old_entities, entities_with_temporary_ids)

                for update in updates:
                    match update.event:
                        case "ADD":
                            entity_id = str(data.next_id)
                            data.next_id += 1
                            data.entities.append(
                                {
                                    "id": entity_id,
                                    "type": entity_type,
                                    "content": update.content,
                                    "created_at": now_iso,
                                    "metadata": update.metadata,
                                }
                            )
                            update.id = entity_id
                        case "UPDATE":
                            for ent in data.entities:
                                if ent["id"] == update.id:
                                    ent["content"] = update.content
                                    ent["created_at"] = now_iso
                                    ent["metadata"] = update.metadata
                                    break
                        case "DELETE":
                            data.entities = [e for e in data.entities if e["id"] != update.id]
                        case "NONE":
                            pass
            else:
                updates = []
                for entity in entities:
                    entity_id = str(data.next_id)
                    data.next_id += 1
                    data.entities.append(
                        {
                            "id": entity_id,
                            "type": entity_type,
                            "content": entity.content,
                            "created_at": now_iso,
                            "metadata": entity.metadata,
                        }
                    )
                    updates.append(
                        EntityUpdate(
                            id=entity_id,
                            type=entity_type,
                            content=entity.content,
                            event="ADD",
                            metadata=entity.metadata,
                        )
                    )

            data.num_entities = len(data.entities)
            self._save_namespace_data(namespace_id, data)

        return updates

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
                raise KaizenException(f"Entity `{entity_id}` not found")
            data.num_entities = len(data.entities)
            self._save_namespace_data(namespace_id, data)
