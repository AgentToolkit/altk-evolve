from dataclasses import dataclass

from pydantic import BaseModel, Field


class GistResponse(BaseModel):
    """LLM response schema for gist generation."""

    gist: str = Field(description="Purpose-directed gist of the conversation")


@dataclass(frozen=True)
class GistResult:
    """Result from generate_gist(), containing one gist per chunk."""

    gists: list[str]
    conversation_id: str | None = None
    message_count: int = 0
    chunk_count: int = 0
