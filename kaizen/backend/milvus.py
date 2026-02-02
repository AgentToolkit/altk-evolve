import datetime
import json
import logging
import uuid

from kaizen.backend.base import BaseEntityBackend, BaseSettings
from kaizen.config.milvus import milvus_client_settings, milvus_other_settings
from kaizen.db.sqlite_manager import SQLiteManager
from kaizen.llm.conflict_resolution.conflict_resolution import resolve_conflicts
from kaizen.schema.core import Namespace, Entity, RecordedEntity
from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.exceptions import NamespaceNotFoundException, KaizenException
from pymilvus import MilvusClient, CollectionSchema, DataType, FieldSchema
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("entities-db.milvus")


def serialize_content(content) -> str:
    """Serialize content to string for Milvus storage."""
    if isinstance(content, str):
        return content
    return json.dumps(content)


def deserialize_content(content: str):
    """Deserialize content from Milvus storage."""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content


def _escape_filter(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class MilvusEntityBackend(BaseEntityBackend):
    milvus: MilvusClient
    embedding_model: SentenceTransformer

    def __init__(self, config: BaseSettings | None = None):
        super().__init__(config)
        self.milvus = MilvusClient(**milvus_client_settings.model_dump())
        self.embedding_model = SentenceTransformer(milvus_other_settings.embedding_model)

    def ready(self) -> bool:
        _ = self.milvus.list_collections()
        return True

    def details(self) -> dict:
        """Return details about the backend."""
        return {}

    def validate_namespace(self, namespace_id: str):
        if not self.milvus.has_collection(namespace_id):
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        """Create a new namespace for entities to exist in."""
        namespace_id = namespace_id or "ns_" + str(uuid.uuid4()).replace("-", "_")

        if not self.milvus.has_collection(namespace_id):
            self.milvus.create_collection(collection_name=namespace_id, dimension=384, auto_id=False, schema=entity_schema)

        with SQLiteManager() as db_manager:
            return db_manager.create_namespace(namespace_id)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        self.validate_namespace(namespace_id)

        with SQLiteManager() as db_manager:
            namespace = db_manager.get_namespace(namespace_id)
            namespace.num_entities = self.milvus.get_collection_stats(namespace_id)["row_count"]
            return namespace

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        with SQLiteManager() as db_manager:
            namespaces = []
            for namespace in db_manager.search_namespaces(limit):
                namespace.num_entities = self.milvus.get_collection_stats(namespace.id)["row_count"]
                namespaces.append(namespace)
            return namespaces

    def delete_namespace(self, namespace_id: str):
        """Delete a namespace that entities exist in."""
        self.milvus.drop_collection(collection_name=namespace_id)

        with SQLiteManager() as db_manager:
            db_manager.delete_namespace(namespace_id)

    def update_entities(self, namespace_id: str, entities: list[Entity], enable_conflict_resolution: bool = True) -> list[EntityUpdate]:
        self.validate_namespace(namespace_id)
        if len(entities) == 0:
            logger.warning("No entities to update.")
            return []

        entity_type = entities[0].type
        if not all(entity.type == entity_type for entity in entities):
            raise KaizenException("All entities must have the same type.")

        now = datetime.datetime.now(datetime.UTC)
        # Use entity's metadata if provided, otherwise default to empty dict for Milvus compatibility
        entities_with_temporary_ids = []
        for i, entity in enumerate(entities):
            entity_data = entity.model_dump()
            if entity_data.get("metadata") is None:
                entity_data["metadata"] = {}
            entities_with_temporary_ids.append(
                RecordedEntity(**entity_data, created_at=datetime.datetime.now(datetime.UTC), id=f"Unprocessed_Entity_{i}")
            )

        if enable_conflict_resolution:
            old_entities = []
            for entity in entities:
                query_str = serialize_content(entity.content)
                old_entities.extend(self.search_entities(namespace_id=namespace_id, query=query_str))

            updates = resolve_conflicts(old_entities, entities_with_temporary_ids)
            for update in updates:
                content_str = serialize_content(update.content)
                match update.event:
                    case "ADD":
                        entity_id = str(
                            self.milvus.insert(
                                collection_name=namespace_id,
                                data={
                                    "type": entity_type,
                                    "content": content_str,
                                    "created_at": int(now.timestamp()),
                                    "embedding": self.embedding_model.encode(content_str),
                                    "metadata": update.metadata,
                                },
                            )["ids"][0]
                        )
                        update.id = entity_id
                    case "UPDATE":
                        self.milvus.upsert(
                            collection_name=namespace_id,
                            data={
                                "type": entity_type,
                                "id": int(update.id),
                                "content": content_str,
                                "created_at": int(now.timestamp()),
                                "embedding": self.embedding_model.encode(content_str),
                                "metadata": update.metadata,
                            },
                            partial_update=True,
                        )
                    case "DELETE":
                        self.delete_entity_by_id(namespace_id=namespace_id, entity_id=update.id)
                    case "NONE":
                        pass
        else:
            updates = []
            for entity in entities:
                content_str = serialize_content(entity.content)
                entity_id = str(
                    self.milvus.insert(
                        collection_name=namespace_id,
                        data={
                            "type": entity_type,
                            "content": content_str,
                            "created_at": int(now.timestamp()),
                            "embedding": self.embedding_model.encode(content_str),
                            "metadata": entity.metadata,
                        },
                    )["ids"][0]
                )
                updates.append(EntityUpdate(id=entity_id, type=entity_type, content=entity.content, event="ADD", metadata=entity.metadata))
        return updates

    def search_entities(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        self.validate_namespace(namespace_id)
        filters = filters or {}

        if query is None:
            # Default query: Get all entities
            results = self.milvus.query(
                collection_name=namespace_id,
                filter=" AND ".join([f"{_escape_filter(k)} == '{_escape_filter(v)}'" for k, v in filters.items()])
                if len(filters) > 0
                else "id > 0",
            )
        else:
            results = self.milvus.query(
                collection_name=namespace_id,
                anns_field="embedding",
                data=[self.embedding_model.encode(query)],
                filter=" AND ".join([f"{_escape_filter(k)} == '{_escape_filter(v)}'" for k, v in filters.items()]),
                limit=limit,
                search_params={"metric_type": "IP"},
            )
        return [parse_milvus_entity(i) for i in results]

    def delete_entity_by_id(self, namespace_id: str, entity_id: str):
        try:
            entity_id_int = int(entity_id)
        except ValueError:
            raise KaizenException(f"Invalid entity ID: {entity_id}. Entity IDs must be numeric.")
        self.validate_namespace(namespace_id)
        # Entity deletion is idempotent and does not require validation.
        self.milvus.delete(collection_name=namespace_id, ids=[entity_id_int])

    def close(self):
        """Close Milvus connection."""
        try:
            if hasattr(self, "milvus"):
                self.milvus.close()
        except Exception as e:
            logger.warning(f"Error closing Milvus client: {e}")


entity_schema = CollectionSchema(
    fields=[
        # Keep it as an INT64 or else you won't be able to list all entities.
        FieldSchema(name="id", is_primary=True, auto_id=True, dtype=DataType.INT64, max_length=128),
        FieldSchema(name="type", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="created_at", dtype=DataType.INT64),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=384),
        FieldSchema(name="metadata", dtype=DataType.JSON),
    ]
)


def parse_milvus_entity(entity: dict) -> RecordedEntity:
    return RecordedEntity.model_validate(
        {
            **entity,
            "id": str(entity["id"]),
            "content": deserialize_content(entity["content"]),
            "created_at": datetime.datetime.fromtimestamp(entity["created_at"], datetime.UTC),
        }
    )
