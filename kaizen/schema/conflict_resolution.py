from kaizen.schema.core import RecordedEntity
from pydantic import BaseModel, Field
from typing import Literal

class SimpleEntity(BaseModel):
    """Derived from either a `Entity` or `RecordedEntity`. Optimized for LLM-based conflict resolution."""
    id: str = Field(description='The unique ID of an entity.')
    type: str = Field(description='The type of the entity.')
    content: str | list | dict = Field(description='The content of the entity.')

    @staticmethod
    def from_recorded_entities(entities: list[RecordedEntity]) -> list['SimpleEntity']:
        return [SimpleEntity(id=entity.id, type=entity.type, content=entity.content) for entity in entities]


class EntityUpdate(BaseModel):
    """Produced by the LLM, to be processed by a kata backend."""
    id: str = Field(description='The unique ID of an entity.')
    type: str = Field(description='The type of the entity.')
    content: str | list | dict = Field(description='The content of the entity.')
    event: Literal['ADD', 'UPDATE', 'DELETE', 'NONE'] = Field(description='The type of update operation to perform.')
    old_entity: str | None = Field(default=None, description='The entity before it was updated.')
    metadata: dict | None = Field(default=None, description='Arbitrary metadata which is related to the entity.')