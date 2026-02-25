import datetime
import json
import logging
import os
import uuid

from kaizen.backend.base import BaseEntityBackend, BaseSettings
from kaizen.config.milvus import MilvusDBSettings, milvus_client_settings
from kaizen.db.sqlite_manager import SQLiteManager
from kaizen.llm.conflict_resolution.conflict_resolution import resolve_conflicts
from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.core import Entity, Namespace, RecordedEntity
from kaizen.schema.exceptions import KaizenException, NamespaceNotFoundException
from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient
from pymilvus.exceptions import MilvusException
from pymilvus.milvus_client.index import IndexParams
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("entities-db.milvus")


def serialize_content(content) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content)


def deserialize_content(content: str):
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content


class MilvusEntityBackend(BaseEntityBackend):
    milvus: MilvusClient
    embedding_model: SentenceTransformer
    _schema_filter_fields = {"id", "type", "content", "created_at"}

    def __init__(self, config: BaseSettings | None = None):
        super().__init__(config)
        resolved_config = config if isinstance(config, MilvusDBSettings) else milvus_client_settings
        self.config = resolved_config
        self.sqlite_uri = os.getenv("KAIZEN_SQLITE_PATH") or self.config.sqlite_uri
        self.milvus = MilvusClient(
            uri=self.config.uri,
            user=self.config.user,
            password=self.config.password,
            db_name=self.config.db_name,
            token=self.config.token,
            timeout=self.config.timeout,
        )
        self.embedding_model = SentenceTransformer(self.config.embedding_model)
        self.metric_type = "COSINE"

    def _build_filter_expr(self, filters: dict | None, base_conditions: list[str] | None = None) -> str:
        base_conditions = base_conditions or []
        expressions = list(base_conditions)
        for key, value in (filters or {}).items():
            if value is None:
                continue
            literal = json.dumps(value)
            if key.startswith("metadata."):
                metadata_key = key.split(".", 1)[1]
                expressions.append(f"metadata[{json.dumps(metadata_key)}] == {literal}")
            elif key in self._schema_filter_fields:
                expressions.append(f"{key} == {literal}")
            else:
                expressions.append(f"metadata[{json.dumps(str(key))}] == {literal}")
        return " AND ".join(expressions)

    def _split_filters(self, filters: dict | None) -> tuple[dict, dict]:
        schema_filters: dict = {}
        metadata_filters: dict = {}
        for key, value in (filters or {}).items():
            if key in self._schema_filter_fields:
                schema_filters[key] = value
            elif key.startswith("metadata."):
                metadata_filters[key.split(".", 1)[1]] = value
            else:
                metadata_filters[str(key)] = value
        return schema_filters, metadata_filters

    @staticmethod
    def _entity_matches_filter(entity: RecordedEntity, schema_filters: dict, metadata_filters: dict) -> bool:
        for key, value in schema_filters.items():
            entity_value = getattr(entity, key, None)
            if key == "id":
                if str(entity_value) != str(value):
                    return False
            elif entity_value != value:
                return False

        metadata = entity.metadata or {}
        for key, value in metadata_filters.items():
            if metadata.get(key) != value:
                return False

        return True

    @staticmethod
    def _extract_vector_score(result: dict) -> float | None:
        for key in ("score", "distance", "_distance", "similarity"):
            value = result.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @classmethod
    def _sort_vector_results(cls, results: list[dict], metric_type: str = "COSINE") -> list[dict]:
        if not results:
            return results

        with_scores = []
        without_scores = []
        for idx, result in enumerate(results):
            score = cls._extract_vector_score(result)
            if score is None:
                without_scores.append((idx, result))
            else:
                with_scores.append((score, idx, result))

        if not with_scores:
            return results

        reverse = metric_type.upper() != "L2"
        with_scores.sort(key=lambda item: item[0], reverse=reverse)

        sorted_results = [item[2] for item in with_scores]
        sorted_results.extend(item[1] for item in sorted(without_scores, key=lambda item: item[0]))
        return sorted_results

    @staticmethod
    def _flatten_search_results(results: list) -> list:
        if not results:
            return []
        if isinstance(results[0], list):
            return list(results[0])
        return list(results)

    @staticmethod
    def _normalize_search_hit(hit) -> dict:
        if hasattr(hit, "to_dict"):
            try:
                hit = hit.to_dict()
            except Exception:
                pass

        if not isinstance(hit, dict):
            normalized = {}
            for attr in ("id", "distance", "score"):
                if hasattr(hit, attr):
                    normalized[attr] = getattr(hit, attr)
            entity_attr = getattr(hit, "entity", None)
            if entity_attr is not None and hasattr(entity_attr, "to_dict"):
                try:
                    entity_attr = entity_attr.to_dict()
                except Exception:
                    entity_attr = None
            if isinstance(entity_attr, dict):
                normalized.update(entity_attr)
            return normalized

        entity = hit.get("entity")
        normalized = {}
        if isinstance(entity, dict):
            normalized.update(entity)
        normalized.update(hit)
        normalized.pop("entity", None)
        return normalized

    def ready(self) -> bool:
        _ = self.milvus.list_collections()
        return True

    def details(self) -> dict:
        return {"metric_type": self.metric_type}

    def validate_namespace(self, namespace_id: str):
        if not self.milvus.has_collection(namespace_id):
            raise NamespaceNotFoundException(f"Namespace `{namespace_id}` not found")

    def _ensure_embedding_index(self, namespace_id: str) -> None:
        try:
            existing_indexes = self.milvus.list_indexes(collection_name=namespace_id, field_name="embedding")
            if existing_indexes:
                return
            logger.warning(
                "Missing embedding index for namespace=%s; creating AUTOINDEX (%s)",
                namespace_id,
                self.metric_type,
            )
            index_params = IndexParams()
            index_params.add_index(
                field_name="embedding",
                index_type="AUTOINDEX",
                index_name="embedding_auto_idx",
                metric_type=self.metric_type,
            )
            self.milvus.create_index(collection_name=namespace_id, index_params=index_params)
            self.milvus.load_collection(collection_name=namespace_id)
        except Exception as exc:
            raise KaizenException(f"Failed to ensure embedding index for namespace={namespace_id}: {exc}") from exc

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        namespace_id = namespace_id or "ns_" + str(uuid.uuid4()).replace("-", "_")

        if not self.milvus.has_collection(namespace_id):
            self.milvus.create_collection(collection_name=namespace_id, dimension=384, auto_id=False, schema=entity_schema)
        self._ensure_embedding_index(namespace_id)

        with SQLiteManager(self.sqlite_uri) as db_manager:
            return db_manager.create_namespace(namespace_id)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        self.validate_namespace(namespace_id)

        with SQLiteManager(self.sqlite_uri) as db_manager:
            namespace = db_manager.get_namespace(namespace_id)
            if namespace is None:
                raise NamespaceNotFoundException(f"Namespace {namespace_id} not found")
            namespace.num_entities = self.milvus.get_collection_stats(namespace_id)["row_count"]
            return namespace

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        with SQLiteManager(self.sqlite_uri) as db_manager:
            namespaces = []
            for namespace in db_manager.search_namespaces(limit):
                namespace.num_entities = self.milvus.get_collection_stats(namespace.id)["row_count"]
                namespaces.append(namespace)
            return namespaces

    def delete_namespace(self, namespace_id: str):
        self.milvus.drop_collection(collection_name=namespace_id)

        with SQLiteManager(self.sqlite_uri) as db_manager:
            db_manager.delete_namespace(namespace_id)

    def update_entities(self, namespace_id: str, entities: list[Entity], enable_conflict_resolution: bool = True) -> list[EntityUpdate]:
        self.validate_namespace(namespace_id)
        if not entities:
            logger.warning("No entities to update.")
            return []

        entity_type = entities[0].type
        if not all(entity.type == entity_type for entity in entities):
            raise KaizenException("All entities must have the same type.")

        now = datetime.datetime.now(datetime.UTC)
        entities_with_temporary_ids: list[RecordedEntity] = []
        for i, entity in enumerate(entities):
            entity_data = entity.model_dump()
            if entity_data.get("metadata") is None:
                entity_data["metadata"] = {}
            entities_with_temporary_ids.append(
                RecordedEntity(
                    **entity_data,
                    created_at=datetime.datetime.now(datetime.UTC),
                    id=f"Unprocessed_Entity_{i}",
                )
            )

        if enable_conflict_resolution:
            old_entities = []
            for entity in entities:
                query_str = serialize_content(entity.content)
                old_entities.extend(
                    self.search_entities(
                        namespace_id=namespace_id,
                        query=query_str,
                        filters={"type": entity_type},
                        limit=10,
                    )
                )

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
                                    "metadata": update.metadata or {},
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
                                "metadata": update.metadata or {},
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
                            "metadata": entity.metadata or {},
                        },
                    )["ids"][0]
                )
                updates.append(
                    EntityUpdate(
                        id=entity_id,
                        type=entity_type,
                        content=entity.content,
                        event="ADD",
                        metadata=entity.metadata or {},
                    )
                )
        self.milvus.flush(namespace_id)
        self.milvus.load_collection(namespace_id)
        return updates

    def search_entities(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        self.validate_namespace(namespace_id)
        filters = filters or {}
        schema_filters, metadata_filters = self._split_filters(filters)
        fetch_limit = max(limit, 1000) if filters else limit

        if query is None:
            try:
                results = self.milvus.query(
                    collection_name=namespace_id,
                    filter=self._build_filter_expr(schema_filters, base_conditions=["id > 0"]),
                    output_fields=["id", "type", "content", "created_at", "metadata"],
                    limit=fetch_limit,
                )
            except MilvusException as exc:
                if "HasRawData" in str(exc):
                    logger.warning(
                        "Milvus raw-data assertion for namespace=%s; returning empty results.",
                        namespace_id,
                    )
                    return []
                raise
        else:
            self._ensure_embedding_index(namespace_id)
            try:
                raw_results = self.milvus.search(
                    collection_name=namespace_id,
                    anns_field="embedding",
                    data=[self.embedding_model.encode(query)],
                    filter=self._build_filter_expr(schema_filters),
                    limit=fetch_limit,
                    output_fields=["*"],
                    search_params={"metric_type": self.metric_type},
                )
            except Exception as exc:
                if "index not found" in str(exc).lower():
                    self._ensure_embedding_index(namespace_id)
                    raw_results = self.milvus.search(
                        collection_name=namespace_id,
                        anns_field="embedding",
                        data=[self.embedding_model.encode(query)],
                        filter=self._build_filter_expr(schema_filters),
                        limit=fetch_limit,
                        output_fields=["*"],
                        search_params={"metric_type": self.metric_type},
                    )
                else:
                    raise
            flat_results = self._flatten_search_results(raw_results)
            normalized = [self._normalize_search_hit(hit) for hit in flat_results]
            results = self._sort_vector_results(normalized, metric_type=self.metric_type)
        parsed = [parse_milvus_entity(i) for i in results]
        filtered = [entity for entity in parsed if self._entity_matches_filter(entity, schema_filters, metadata_filters)]
        return filtered[:limit]

    def delete_entity_by_id(self, namespace_id: str, entity_id: str):
        try:
            entity_id_int = int(entity_id)
        except ValueError as exc:
            raise KaizenException(f"Invalid entity ID: {entity_id}. Entity IDs must be numeric.") from exc
        self.validate_namespace(namespace_id)
        self.milvus.delete(collection_name=namespace_id, ids=[entity_id_int])

    def close(self):
        try:
            if hasattr(self, "milvus"):
                self.milvus.close()
        except Exception as exc:
            logger.warning("Error closing Milvus client: %s", exc)


entity_schema = CollectionSchema(
    fields=[
        FieldSchema(name="id", is_primary=True, auto_id=True, dtype=DataType.INT64),
        FieldSchema(name="type", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="created_at", dtype=DataType.INT64),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=384),
        FieldSchema(name="metadata", dtype=DataType.JSON),
    ]
)


def parse_milvus_entity(entity: dict) -> RecordedEntity:
    metadata = entity.get("metadata", {}) or {}
    return RecordedEntity.model_validate(
        {
            **entity,
            "id": str(entity["id"]),
            "content": deserialize_content(entity.get("content", "")),
            "metadata": metadata,
            "created_at": datetime.datetime.fromtimestamp(int(entity["created_at"]), datetime.UTC),
        }
    )
