import logging
from abc import ABC, abstractmethod
from pydantic_settings import BaseSettings
from kaizen.schema.core import Namespace, Entity, RecordedEntity
from kaizen.schema.conflict_resolution import EntityUpdate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("entities-db")


class BaseEntityBackend(ABC):
    def __init__(self, config: BaseSettings | None = None):
        pass

    @abstractmethod
    def ready(self) -> bool:
        pass

    @abstractmethod
    def details(self) -> dict:
        pass

    @abstractmethod
    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        pass

    @abstractmethod
    def get_namespace_details(self, namespace_id: str) -> Namespace:
        pass

    @abstractmethod
    def delete_namespace(self, namespace_id: str):
        pass

    @abstractmethod
    def update_entities(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True,
    ) -> list[EntityUpdate]:
        pass

    def search_entities(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        pass

    @abstractmethod
    def delete_entity_by_id(self, namespace_id: str, entity_id: str):
        pass
