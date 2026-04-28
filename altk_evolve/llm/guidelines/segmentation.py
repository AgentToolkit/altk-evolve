import json
import logging
from json import JSONDecodeError
from pathlib import Path

import litellm
from jinja2 import Template
from litellm import completion, get_supported_openai_params, supports_response_schema
from pydantic import ValidationError

from altk_evolve.config.llm import llm_settings
from altk_evolve.schema.guidelines import SegmentationResponse, SubtaskSegment
from altk_evolve.utils.utils import clean_llm_response

logger = logging.getLogger(__name__)

_SEGMENT_TEMPLATE = Template((Path(__file__).parent / "prompts/segment_trajectory.jinja2").read_text())


def segment_trajectory(messages: list[dict]) -> list[SubtaskSegment]:
    """Segment a trajectory into logical subtasks with generalized descriptions.

    The returned start_step/end_step are 1-based indices into the filtered
    reasoning+action steps_list from parse_openai_agents_trajectory(messages),
    NOT into raw messages. Callers must slice that same steps_list — slicing
    raw messages with these indices will misalign content.

    Returns an empty list on failure — callers fall back to full-trajectory guideline generation.
    """
    # Import here to avoid circular import (guidelines.py imports this module)
    from altk_evolve.llm.guidelines.guidelines import parse_openai_agents_trajectory

    trajectory_data = parse_openai_agents_trajectory(messages)

    supported_params = get_supported_openai_params(
        model=llm_settings.guidelines_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    supports_response_format = supported_params and "response_format" in supported_params
    response_schema_enabled = supports_response_schema(
        model=llm_settings.guidelines_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    constrained_decoding_supported = bool(supports_response_format and response_schema_enabled)

    prompt = _SEGMENT_TEMPLATE.render(
        trajectory_summary=trajectory_data["trajectory_summary"],
        num_steps=trajectory_data["num_steps"],
        constrained_decoding_supported=constrained_decoding_supported,
    )

    litellm.enable_json_schema_validation = constrained_decoding_supported

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if constrained_decoding_supported:
                raw = (
                    completion(
                        model=llm_settings.guidelines_model,
                        messages=[{"role": "user", "content": prompt}],
                        response_format=SegmentationResponse,
                        custom_llm_provider=llm_settings.custom_llm_provider,
                    )
                    .choices[0]
                    .message.content
                )
            else:
                raw = (
                    completion(
                        model=llm_settings.guidelines_model,
                        messages=[{"role": "user", "content": prompt}],
                        custom_llm_provider=llm_settings.custom_llm_provider,
                    )
                    .choices[0]
                    .message.content
                )
            clean_response = clean_llm_response(raw)

            if not clean_response:
                logger.debug(f"Segmentation attempt {attempt + 1}: empty response")
                continue

            subtasks = SegmentationResponse.model_validate(json.loads(clean_response)).subtasks
            return subtasks

        except (JSONDecodeError, ValidationError) as e:
            logger.debug(f"Segmentation attempt {attempt + 1} failed: {e}")
            last_error = e
            continue

    logger.warning(f"Failed to segment trajectory after 3 attempts: {last_error}")
    return []
