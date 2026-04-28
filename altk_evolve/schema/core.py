from datetime import datetime
from pydantic import BaseModel, Field
from sqlite3 import Cursor, Row


class Namespace(BaseModel):
    """Details of a namespace containing memories."""

    id: str = Field(description="The unique ID of a namespace.")
    created_at: datetime = Field(description="The time the namespace was created.")
    num_entities: int | None = Field(default=None, description="The number of entities in the namespace. May not be accurate.")

    @staticmethod
    def row_factory(cursor: Cursor, row: Row) -> "Namespace":
        fields = [column[0] for column in cursor.description]
        return Namespace(**{k: v for k, v in zip(fields, row)})


class Entity(BaseModel):
    """Basic data stored in the DB.

    Sharing metadata conventions (all optional; existing entities without them are unaffected):
      - owner_id (str | None): User ID who created or last published the entity.
      - visibility ("private" | "public"): Controls cross-namespace access. Defaults to "private".
      - published_at (ISO-8601 str | None): Timestamp of the most recent publish_entity call.
    """

    content: str | list | dict = Field(description="Searchable text or structured data.")
    type: str = Field(description="The type of the entity.")
    metadata: dict = Field(default_factory=dict, description="Arbitrary metadata which is related to the entity.")


class RecordedEntity(Entity):
    """A statement about a person, place, or thing."""

    id: str = Field(description="The unique ID of an entity.")
    created_at: datetime = Field(description="The date and time the entity was created.")
