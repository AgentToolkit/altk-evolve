import json

from jinja2 import Template
from altk_evolve.config.llm import llm_settings
from altk_evolve.schema.conflict_resolution import SimpleEntity, EntityUpdate
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.utils.utils import clean_llm_response
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
            old_entities_by_id = {entity.id: entity for entity in old_entities}
            added_new_ids = {u.id for u in entity_updates if u.event == "ADD"}
            # New entities not consumed by an ADD were merged into an UPDATE — carry
            # their generation_method provenance forward into the updated entity.
            unmatched_new_entities = [e for e in new_entities if e.id not in added_new_ids]
            for update in entity_updates:
                if update.event == "ADD":
                    update.metadata = new_entities_by_id[update.id].metadata
                elif update.event == "UPDATE":
                    old_meta = dict(old_entities_by_id[update.id].metadata) if update.id in old_entities_by_id else {}
                    incoming_methods = {
                        e.metadata.get("generation_method")
                        for e in unmatched_new_entities
                        if e.metadata.get("generation_method")
                    }
                    old_method = old_meta.get("generation_method")
                    all_methods = ({old_method} if old_method else set()) | incoming_methods
                    merged = dict(old_meta)
                    if len(all_methods) > 1:
                        merged.pop("generation_method", None)
                        merged["generation_methods"] = sorted(all_methods)
                    elif all_methods:
                        merged["generation_method"] = next(iter(all_methods))
                    update.metadata = merged

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
