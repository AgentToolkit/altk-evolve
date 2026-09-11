"""Small third-party processor: no LLM, database, or Evolve source changes."""

from pydantic import BaseModel, ConfigDict, Field

from altk_evolve.processing import ProcessorContext, ProcessorResult, Trajectory
from altk_evolve.schema.core import Entity


class WordCountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_words: int = Field(default=0, ge=0)


class WordCountProcessor:
    id = "example.word_count"
    api_version = 1
    version = "0.1.0"
    config_model = WordCountConfig

    def process(self, trajectory: Trajectory, *, config: WordCountConfig, context: ProcessorContext) -> ProcessorResult:
        count = sum(len(message["content"].split()) for message in trajectory.messages if isinstance(message.get("content"), str))
        if count < config.minimum_words:
            return ProcessorResult(diagnostics={"skipped": True, "word_count": count})
        return ProcessorResult(
            entities=[Entity(type="trajectory_stats", content={"word_count": count}, metadata={"source_task_id": trajectory.trace_id})]
        )
