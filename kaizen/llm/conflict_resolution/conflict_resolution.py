import json

from jinja2 import Template
from kaizen.config.llm import llm_settings
from kaizen.schema.conflict_resolution import SimpleEntity, EntityUpdate
from kaizen.schema.core import RecordedEntity
from kaizen.schema.exceptions import KaizenException
from kaizen.utils.utils import clean_llm_response
from litellm import completion
from pathlib import Path


def resolve_conflicts(
    old_entities: list[RecordedEntity], new_entities: list[RecordedEntity], custom_update_entities_prompt: str | None = None
) -> list[EntityUpdate]:
    simplified_old_entities = SimpleEntity.from_recorded_entities(old_entities)
    simplified_new_entities = SimpleEntity.from_recorded_entities(new_entities)
    new_entities_by_id = {entity.id: entity for entity in new_entities}

    prompt = get_update_entities_messages(simplified_old_entities, simplified_new_entities, custom_update_entities_prompt)

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            completion_response = completion(
                model=llm_settings.conflict_resolution_model,
                messages=[{"role": "user", "content": prompt}],
                custom_llm_provider=llm_settings.custom_llm_provider,
            )
            response = completion_response.choices[0].message.content or ""  # type: ignore[union-attr]
            response = clean_llm_response(response)
            parsed = json.loads(response)
            entity_updates = [EntityUpdate.model_validate(event) for event in parsed["entities"]]
            for update in entity_updates:
                if update.event == "ADD":
                    update.metadata = new_entities_by_id[update.id].metadata

            return entity_updates
        except Exception as e:
            last_error = e
            if attempt < 2:
                continue
    raise KaizenException("Failed to resolve conflicts after 3 attempts") from last_error


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
