import json
import logging
from json import JSONDecodeError
from pathlib import Path

from jinja2 import Template
from litellm import completion, get_supported_openai_params, supports_response_schema
from pydantic import ValidationError

from altk_evolve.config.llm import llm_settings  # noqa: F401
from altk_evolve.config.evolve import evolve_config  # noqa: F401
from altk_evolve.config.guideline_runtime import GuidelineRuntime
from altk_evolve.hooks.manager import dispatch_llm_pre_call
from altk_evolve.schema.guidelines import SegmentationResponse, SubtaskSegment
from altk_evolve.utils.utils import clean_llm_response

logger = logging.getLogger(__name__)

_SEGMENT_TEMPLATE = Template((Path(__file__).parent / "prompts/segment_trajectory.jinja2").read_text())

# Shortest trajectory worth splitting into subtasks. Below this, segmentation costs an extra
# LLM call and mostly yields per-step guidelines, so one whole-trajectory pass is better.
# Every generation path that segments checks it before calling segment_trajectory.
SEGMENTATION_MIN_STEPS = 5


def subtask_slices(subtasks: list[SubtaskSegment], steps_list: list[str]) -> list[tuple[SubtaskSegment, list[str]]]:
    """Pair each subtask with its slice of ``steps_list``, one generation call per slice.

    ``start_step``/``end_step`` are 1-based and inclusive, and are clamped to the list. A
    subtask whose range is empty after clamping, or the same as an earlier subtask's, is
    skipped, so a segmenter that repeats a range cannot fan one slice out into several
    near-identical generation calls.
    """
    n_steps = len(steps_list)
    slices: list[tuple[SubtaskSegment, list[str]]] = []
    seen: set[tuple[int, int]] = set()
    for subtask in subtasks:
        start = min(max(0, subtask.start_step - 1), n_steps)
        end = min(max(0, subtask.end_step), n_steps)
        if start >= end or (start, end) in seen:
            logger.debug(f"Skipping subtask with empty or repeated steps [{subtask.start_step}, {subtask.end_step}] (n_steps={n_steps})")
            continue
        seen.add((start, end))
        slices.append((subtask, steps_list[start:end]))
    return slices


def one_pass_task_description(slices: list[tuple[SubtaskSegment, list[str]]], task_instruction: str) -> str:
    """``task_description`` for the single whole-trajectory call, given ``subtask_slices``' output.

    With exactly one distinct step range left, that subtask's generalized description is kept,
    however many copies of the range the segmenter returned. With none (segmentation off or
    skipped, the segmenter failed or returned nothing usable), it is the first user message
    verbatim.
    """
    return slices[0][0].generalized_description if len(slices) == 1 else task_instruction


def segment_trajectory(messages: list[dict], *, options: GuidelineRuntime | None = None) -> list[SubtaskSegment]:
    """Segment a trajectory into logical subtasks with generalized descriptions.

    The returned start_step/end_step are 1-based indices into the filtered
    reasoning+action steps_list from parse_openai_agents_trajectory(messages),
    NOT into raw messages. Callers must slice that same steps_list — slicing
    raw messages with these indices will misalign content.

    Returns an empty list on failure — callers fall back to full-trajectory guideline generation.
    """
    options = options or GuidelineRuntime.from_settings()
    # Import here to avoid circular import (guidelines.py imports this module)
    from altk_evolve.llm.guidelines.guidelines import parse_openai_agents_trajectory

    trajectory_data = parse_openai_agents_trajectory(messages)

    supported_params = get_supported_openai_params(
        model=options.guidelines_model,
        custom_llm_provider=options.custom_llm_provider,
    )
    supports_response_format = supported_params and "response_format" in supported_params
    response_schema_enabled = supports_response_schema(
        model=options.guidelines_model,
        custom_llm_provider=options.custom_llm_provider,
    )
    constrained_decoding_supported = bool(supports_response_format and response_schema_enabled)

    prompt = _SEGMENT_TEMPLATE.render(
        trajectory_summary=trajectory_data["trajectory_summary"],
        num_steps=trajectory_data["num_steps"],
        constrained_decoding_supported=constrained_decoding_supported,
    )

    llm_messages = dispatch_llm_pre_call([{"role": "user", "content": prompt}], purpose="segmentation", model=options.guidelines_model)

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if constrained_decoding_supported:
                raw = (
                    completion(
                        model=options.guidelines_model,
                        messages=llm_messages,
                        response_format=SegmentationResponse,
                        custom_llm_provider=options.custom_llm_provider,
                        enable_json_schema_validation=constrained_decoding_supported,
                    )
                    .choices[0]
                    .message.content
                )
            else:
                raw = (
                    completion(
                        model=options.guidelines_model,
                        messages=llm_messages,
                        custom_llm_provider=options.custom_llm_provider,
                        enable_json_schema_validation=constrained_decoding_supported,
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
