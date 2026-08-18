import json

from jinja2 import Template
from altk_evolve.config.llm import llm_settings
from altk_evolve.hooks.manager import dispatch_llm_pre_call
from altk_evolve.schema.conflict_resolution import SimpleEntity, EntityUpdate
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.utils.utils import clean_llm_response, serialize_content
from litellm import completion, get_supported_openai_params
from pathlib import Path


# Metadata keys that record how the STORED entity was originally created and so
# must survive an UPDATE unchanged, even when the incoming entity carries a
# different value. Per-write stamps (trace_id, last_accessed) are deliberately
# NOT listed: those should refresh to the incoming write's normalized values.
_STICKY_STORED_METADATA_KEYS = ("generation_method",)

_GROQ_GPT_OSS_CONFLICT_MAX_TOKENS = 8192


def _conflict_resolution_completion_options() -> dict[str, object]:
    """Per-provider completion options for conflict resolution.

    Two independent concerns:
    - GPT-OSS on Groq spends the completion budget on reasoning tokens, leaving
      nothing for the conflict JSON, so cap tokens and lower reasoning effort.
    - Ask for JSON mode where the provider handles it, so the reply is
      constrained at the source instead of being recovered afterwards.

    Groq is deliberately excluded from response_format on the same grounds as
    guideline generation (#264): LiteLLM reports response-format support for
    groq/openai/gpt-oss while the model can still fail when a schema- or
    tool-backed response is required. Groq therefore stays on the
    prompt-and-parse path, protected by the token budget above and by
    clean_llm_response recovery. Do not enable it here without live-testing
    groq/openai/gpt-oss-120b first.
    """
    model = llm_settings.conflict_resolution_model.strip().lower()
    provider = (llm_settings.custom_llm_provider or "").strip().lower()
    is_groq = provider == "groq" or model.startswith("groq/")

    options: dict[str, object] = {}
    if is_groq:
        if "gpt-oss" in model:
            options["max_tokens"] = _GROQ_GPT_OSS_CONFLICT_MAX_TOKENS
            options["reasoning_effort"] = "low"
        return options

    supported_params = get_supported_openai_params(
        model=llm_settings.conflict_resolution_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    if supported_params and "response_format" in supported_params:
        # Plain JSON mode, not a schema: the prompt already fixes the shape, and
        # json_object needs no tool routing.
        options["response_format"] = {"type": "json_object"}
    return options


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
                **_conflict_resolution_completion_options(),
            )
            choice = completion_response.choices[0]
            # A budget-capped reply is a different failure from a malformed one and
            # needs a different fix (raise the budget or send fewer entities), so
            # name it rather than surfacing a generic JSON error. Checked only when
            # the content does not parse: a reply can stop exactly at the limit and
            # still be complete, and rejecting that would throw away good output.
            truncated = str(getattr(choice, "finish_reason", "") or "") == "length"
            response = clean_llm_response(choice.message.content or "")  # type: ignore[union-attr]
            if not response:
                raise ValueError(
                    "Conflict resolution LLM returned an empty response"
                    + (" because the completion budget was spent before any JSON (finish_reason=length)" if truncated else "")
                )
            try:
                parsed = json.loads(response)
            except json.JSONDecodeError as decode_error:
                if truncated:
                    raise ValueError(
                        "Conflict resolution response was cut off by the completion"
                        " budget (finish_reason=length); raise max_tokens or send"
                        " fewer entities per request"
                    ) from decode_error
                raise
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
                    merged = {**stored_metadata, **incoming_metadata}
                    # Sticky ADD-time provenance (#289): generation_method records HOW the
                    # stored entity was originally produced, so unlike per-write stamps
                    # (trace_id) it must not be overwritten by the incoming entity.
                    for sticky_key in _STICKY_STORED_METADATA_KEYS:
                        if sticky_key in stored_metadata:
                            merged[sticky_key] = stored_metadata[sticky_key]
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
