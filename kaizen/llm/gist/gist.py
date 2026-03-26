import json
import logging
from pathlib import Path

import litellm
from jinja2 import Template
from litellm import completion, get_supported_openai_params, supports_response_schema

from kaizen.config.kaizen import kaizen_config
from kaizen.config.llm import llm_settings
from kaizen.schema.gist import GistResponse, GistResult
from kaizen.utils.utils import clean_llm_response

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = Template((Path(__file__).parent / "prompts/generate_gist.jinja2").read_text())


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _chunk_messages(messages: list[dict], context_budget: int) -> list[list[dict]]:
    """Split messages into chunks that fit within the context budget.

    Returns a list of message chunks. Most sessions will produce a single chunk.
    """
    chunks: list[list[dict]] = []
    current_chunk: list[dict] = []
    current_tokens = 0

    # Reserve tokens for prompt template + response
    available_tokens = context_budget - 2000

    for message in messages:
        content = message.get("content", "")
        if isinstance(content, list):
            content = str(content)
        msg_tokens = _estimate_tokens(str(content))

        if current_chunk and (current_tokens + msg_tokens) > available_tokens:
            chunks.append(current_chunk)
            current_chunk = []
            current_tokens = 0

        current_chunk.append(message)
        current_tokens += msg_tokens

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _generate_single_gist(messages: list[dict], constrained_decoding_supported: bool) -> str | None:
    """Generate a gist for a single chunk of messages. Returns the gist string or None on failure."""
    prompt = _PROMPT_TEMPLATE.render(
        messages=messages,
        constrained_decoding_supported=constrained_decoding_supported,
    )

    last_error = None
    for _ in range(3):
        try:
            if constrained_decoding_supported:
                litellm.enable_json_schema_validation = True
                raw = (
                    completion(
                        model=llm_settings.gist_model,
                        messages=[{"role": "user", "content": prompt}],
                        response_format=GistResponse,
                        custom_llm_provider=llm_settings.custom_llm_provider,
                    )
                    .choices[0]
                    .message.content
                )
            else:
                litellm.enable_json_schema_validation = False
                raw = (
                    completion(
                        model=llm_settings.gist_model,
                        messages=[{"role": "user", "content": prompt}],
                        custom_llm_provider=llm_settings.custom_llm_provider,
                    )
                    .choices[0]
                    .message.content
                )
                raw = clean_llm_response(raw)

            if not raw:
                logger.warning("LLM returned empty response for gist generation.")
                return None

            parsed = GistResponse.model_validate(json.loads(raw))
            return parsed.gist
        except Exception as exc:
            last_error = exc
            continue

    logger.warning(f"Failed to generate gist after 3 attempts: {last_error}")
    return None


def generate_gist(messages: list[dict], conversation_id: str | None = None) -> GistResult:
    """Generate purpose-directed gists from conversation messages.

    Messages are chunked based on the context budget. Each chunk produces one gist.
    Most sessions fit in a single chunk, producing one consolidated gist.
    """
    if not messages:
        return GistResult(gists=[], conversation_id=conversation_id, message_count=0, chunk_count=0)

    supported_params = get_supported_openai_params(
        model=llm_settings.gist_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    supports_response_format = supported_params and "response_format" in supported_params
    response_schema_enabled = supports_response_schema(
        model=llm_settings.gist_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    constrained_decoding_supported = supports_response_format and response_schema_enabled

    chunks = _chunk_messages(messages, kaizen_config.gist_context_budget)
    gists: list[str] = []

    for chunk in chunks:
        gist = _generate_single_gist(chunk, constrained_decoding_supported)
        if gist and gist != "no user signal":
            gists.append(gist)

    return GistResult(
        gists=gists,
        conversation_id=conversation_id,
        message_count=len(messages),
        chunk_count=len(chunks),
    )
