import json

from jinja2 import Template
from altk_evolve.config.llm import llm_settings
from altk_evolve.hooks.manager import dispatch_llm_pre_call
from altk_evolve.schema.conflict_resolution import SimpleEntity, EntityUpdate
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.utils.utils import clean_llm_response, serialize_content
from litellm import completion
from pathlib import Path


def resolve_conflicts(
    old_entities: list[RecordedEntity], new_entities: list[RecordedEntity], custom_update_entities_prompt: str | None = None
) -> list[EntityUpdate]:
    simplified_old_entities = SimpleEntity.from_recorded_entities(old_entities)
    simplified_new_entities = SimpleEntity.from_recorded_entities(new_entities)
    new_entities_by_id = {entity.id: entity for entity in new_entities}
    # UPDATE verdicts carry the OLD stored id (kept unchanged, per the prompt),
    # not the temp id new_entities_by_id is keyed by, so map incoming entities
    # by serialized content to trace an UPDATE back to the source. Also index
    # the stored entities (already fetched via the internal read seam) by id so
    # we can preserve their existing metadata as a fallback / merge base.
    new_entities_by_content = {serialize_content(entity.content): entity for entity in new_entities}
    old_entities_by_id = {entity.id: entity for entity in old_entities}

    prompt = get_update_entities_messages(simplified_old_entities, simplified_new_entities, custom_update_entities_prompt)
    llm_messages = dispatch_llm_pre_call(
        [{"role": "user", "content": prompt}], purpose="conflict_resolution", model=llm_settings.conflict_resolution_model
    )

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            completion_response = completion(
                model=llm_settings.conflict_resolution_model,
                messages=llm_messages,
                custom_llm_provider=llm_settings.custom_llm_provider,
            )
            response = completion_response.choices[0].message.content or ""  # type: ignore[union-attr]
            response = clean_llm_response(response)
            parsed = json.loads(response)
            entity_updates = [EntityUpdate.model_validate(event) for event in parsed["entities"]]
            for update in entity_updates:
                if update.event == "ADD":
                    update.metadata = new_entities_by_id[update.id].metadata
                elif update.event == "UPDATE":
                    # base._update_entity does a WHOLESALE metadata replace, so
                    # leaving update.metadata={} on an UPDATE destroys
                    # plugin-written metadata (normalizer trace_id/created_at,
                    # access-stamp last_accessed). Thread metadata through:
                    # prefer the incoming entity's metadata (which already passed
                    # through memory_pre_write, so it carries the normalized/
                    # stamped values), matched by content, merged OVER the stored
                    # entity's existing metadata so stored-only stamps (e.g.
                    # last_accessed) also survive. If the source incoming entity
                    # can't be identified, fall back to preserving the stored
                    # metadata alone.
                    stored = old_entities_by_id.get(update.id)
                    stored_metadata = (stored.metadata or {}) if stored is not None else {}
                    source = new_entities_by_content.get(serialize_content(update.content))
                    incoming_metadata = (source.metadata or {}) if source is not None else {}
                    update.metadata = {**stored_metadata, **incoming_metadata}

            return entity_updates
        except Exception as e:
            last_error = e
            if attempt < 2:
                continue
    raise EvolveException("Failed to resolve conflicts after 3 attempts") from last_error


def get_update_entities_messages(
    old_entities: list["SimpleEntity"],
    new_entities: list["SimpleEntity"],
    custom_update_entities_prompt: str | None = None,
) -> str:
    if custom_update_entities_prompt is None:
        prompt_file = Path(__file__).parent / "prompts/default_conflict_resolution.jinja2"
        custom_update_entities_prompt = Template(prompt_file.read_text()).render()

    prompt_input = {
        "custom_update_entities_prompt": custom_update_entities_prompt,
        "old_entities": json.dumps([entity.model_dump(mode="json") for entity in old_entities], indent=4),
        "new_entities": json.dumps([entity.model_dump(mode="json") for entity in new_entities], indent=4),
    }
    prompt_file = Path(__file__).parent / "prompts/conflict_resolution.jinja2"

    return Template(prompt_file.read_text()).render(**prompt_input)
