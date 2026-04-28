import json
import logging
from json import JSONDecodeError
from pathlib import Path

import litellm
from jinja2 import Template
from litellm import completion, get_supported_openai_params, supports_response_schema
from pydantic import ValidationError

from altk_evolve.config.evolve import evolve_config
from altk_evolve.config.llm import llm_settings
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.schema.guidelines import DEFAULT_TASK_DESCRIPTION, GuidelineGenerationResponse, GuidelineGenerationResult
from altk_evolve.utils.utils import clean_llm_response

logger = logging.getLogger(__name__)

_GENERATE_GUIDELINES_TEMPLATE = Template((Path(__file__).parent / "prompts/generate_guidelines.jinja2").read_text())


def parse_openai_agents_trajectory(messages: list[dict]) -> dict:
    """
    Parse OpenAI Agents SDK trajectory from streamer.to_input_list().

    Returns:
        dict with:
        - task_instruction: The task description
        - agent_steps: List of agent reasoning/actions
        - function_calls: List of tool/function calls made
        - num_steps: Total number of agent actions
        - steps_list: Individual formatted step strings (before joining), for subtask slicing
    """
    agent_steps: list[dict[str, str | dict]] = []
    function_calls: list[dict[str, str | dict]] = []
    task_instruction: str | None = None

    for message in messages:
        # Extract task instruction from first user message
        if message.get("role") == "user" and task_instruction is None:
            if isinstance(message["content"], str):
                task_instruction = message["content"]
            else:
                raise EvolveException("First user message was not a task instruction.")

        # Extract assistant reasoning/messages
        if message.get("role") == "assistant":
            content = message.get("content", "")
            if isinstance(content, str) and content.strip():
                agent_steps.append({"type": "reasoning", "content": content, "raw": message})

            # Extract function calls
            elif isinstance(content, list):
                for assistant_response in content:
                    if assistant_response["type"] == "function_call":
                        function_call = {
                            "type": "function_call",
                            "name": assistant_response["function"]["name"],
                            "arguments": assistant_response["function"]["arguments"],
                            "call_id": assistant_response["id"],
                            "raw": assistant_response,
                        }
                        function_calls.append(function_call)

                        # Add to agent steps as an action
                        args_str = assistant_response["function"]["arguments"]
                        try:
                            args: dict = json.loads(args_str)
                            args_display = ", ".join(f"{k}={json.dumps(v)}" for k, v in args.items())
                            function_description = f"{assistant_response['function']['name']}({args_display})"
                        except JSONDecodeError:
                            function_description = f"{assistant_response['function']['name']}({args_str})"

                        agent_steps.append(
                            {
                                "type": "action",
                                "content": function_description,
                                "raw": assistant_response,
                            }
                        )
                    else:
                        raise EvolveException(f"Unhandled assistant content type in list `{assistant_response['type']}`")
            else:
                # Skip empty assistant messages (common from tool-calling patterns)
                continue

    steps_list = []
    for i, step in enumerate(agent_steps[:50], 1):
        step_type = step["type"]
        content = step["content"]
        # Truncate long content
        if len(content) > 2000:
            content = content[:2000] + "..."

        if step_type == "reasoning":
            steps_list.append(f"**Step {i} - Reasoning:**\n{content}")
        elif step_type == "action":
            steps_list.append(f"**Step {i} - Action:**\n{content}")

    return {
        "task_instruction": task_instruction or DEFAULT_TASK_DESCRIPTION,
        "trajectory_summary": "\n\n".join(steps_list),
        "steps_list": steps_list,
        "function_calls": function_calls,
        "num_steps": len([s for s in agent_steps[:50] if s["type"] in ["action", "reasoning"]]),
    }


def _generate_guidelines_for_segment(
    task_description: str,
    trajectory_slice: str,
    num_steps: int,
    constrained_decoding_supported: bool,
) -> GuidelineGenerationResult:
    """Generate guidelines for a single trajectory slice (full or subtask)."""
    prompt = _GENERATE_GUIDELINES_TEMPLATE.render(
        task_instruction=task_description,
        num_steps=num_steps,
        trajectory_summary=trajectory_slice,
        constrained_decoding_supported=constrained_decoding_supported,
    )

    if constrained_decoding_supported:
        litellm.enable_json_schema_validation = True
        raw = (
            completion(
                model=llm_settings.guidelines_model,
                messages=[{"role": "user", "content": prompt}],
                response_format=GuidelineGenerationResponse,
                custom_llm_provider=llm_settings.custom_llm_provider,
            )
            .choices[0]
            .message.content
        )
    else:
        litellm.enable_json_schema_validation = False
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
        logger.warning(f"LLM returned empty response for guideline generation. Model: {llm_settings.guidelines_model}")
        return GuidelineGenerationResult(guidelines=[], task_description=task_description)
    try:
        guidelines = GuidelineGenerationResponse.model_validate(json.loads(clean_response)).guidelines
        return GuidelineGenerationResult(guidelines=guidelines, task_description=task_description)
    except JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM guideline generation response: {e}. Response: {repr(clean_response[:500])}")
        return GuidelineGenerationResult(guidelines=[], task_description=task_description)
    except ValidationError as e:
        logger.warning(f"Failed to validate LLM guideline generation response: {e}. Response: {repr(clean_response[:500])}")
        return GuidelineGenerationResult(guidelines=[], task_description=task_description)


def generate_guidelines(messages: list[dict]) -> list[GuidelineGenerationResult]:
    """Generate guidelines from a trajectory, optionally segmented into subtasks.

    When segmentation is enabled (EVOLVE_SEGMENTATION_ENABLED=true, the default),
    the trajectory is first segmented into logical subtasks. Guidelines are then generated
    per subtask and each result carries the subtask's generalized description as
    task_description — giving downstream clustering much more precise signal than
    the raw first user message.

    Returns a list with one GuidelineGenerationResult per subtask (or one for the full
    trajectory when segmentation is disabled or produces fewer than 2 subtasks).
    """
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

    trajectory_data = parse_openai_agents_trajectory(messages)
    task_instruction = trajectory_data["task_instruction"]
    steps_list: list[str] = trajectory_data["steps_list"]
    n_steps = len(steps_list)

    subtasks = []
    if evolve_config.segmentation_enabled:
        from altk_evolve.llm.guidelines.segmentation import segment_trajectory  # avoid circular import

        try:
            subtasks = segment_trajectory(messages)
        except Exception as e:
            logger.warning(f"Trajectory segmentation failed, falling back to full trajectory: {e}")
            subtasks = []

    if len(subtasks) >= 2:
        valid_slices: list[tuple] = []
        for subtask in subtasks:
            start = min(max(0, subtask.start_step - 1), n_steps)
            end = min(max(0, subtask.end_step), n_steps)
            if start >= end:
                logger.debug(f"Skipping subtask with out-of-range steps [{subtask.start_step}, {subtask.end_step}] (n_steps={n_steps})")
                continue
            valid_slices.append((subtask, steps_list[start:end]))

        if len(valid_slices) >= 2:
            return [
                _generate_guidelines_for_segment(
                    task_description=subtask.generalized_description,
                    trajectory_slice="\n\n".join(slice_steps),
                    num_steps=len(slice_steps),
                    constrained_decoding_supported=constrained_decoding_supported,
                )
                for subtask, slice_steps in valid_slices
            ]
        # Fewer than 2 valid subtask slices — fall through to full-trajectory fallback.

    # Fallback: full trajectory (use segmented description if exactly 1 subtask was found)
    desc = subtasks[0].generalized_description if len(subtasks) == 1 else task_instruction
    return [
        _generate_guidelines_for_segment(
            task_description=desc,
            trajectory_slice=trajectory_data["trajectory_summary"],
            num_steps=trajectory_data["num_steps"],
            constrained_decoding_supported=constrained_decoding_supported,
        )
    ]
