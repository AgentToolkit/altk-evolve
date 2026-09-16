import json
import logging
import re
from json import JSONDecodeError
from pathlib import Path

import litellm
from jinja2 import Template
from litellm import completion, get_supported_openai_params, supports_response_schema
from pydantic import ValidationError

from altk_evolve.config.evolve import evolve_config
from altk_evolve.config.llm import llm_settings
from altk_evolve.hooks.manager import dispatch_llm_pre_call
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.schema.guidelines import (
    DEFAULT_TASK_DESCRIPTION,
    Guideline,
    GuidelineGenerationResponse,
    GuidelineGenerationResult,
)
from altk_evolve.utils.utils import clean_llm_response

logger = logging.getLogger(__name__)

_GENERATE_GUIDELINES_TEMPLATE = Template((Path(__file__).parent / "prompts/generate_guidelines.jinja2").read_text())

# Matches one escape at a time: group 1 is a complete, valid JSON escape (\u only counts
# when four hex digits follow, so LaTeX like \underbrace is repaired rather than left as
# an invalid \u that fails to parse either way); group 2 is any other escaped character,
# i.e. a lone backslash needing doubling. Valid pairs are *consumed*, not looked past —
# with a lookahead, the second half of a correct \\ was re-examined as the start of a new
# escape, so `\\d` became `\\\d` and the response stayed unparseable. A trailing lone
# backslash matches group 2 as empty and is still doubled.
_JSON_ESCAPE_RE = re.compile(r'\\(?:(["\\/bfnrt]|u[0-9a-fA-F]{4})|(.|$))', re.DOTALL)

# C0 control characters, which a JSON string can only carry via an escape sequence.
_C0_CONTROL = {chr(code) for code in range(0x20)}


def _escape_lone_backslashes(text: str) -> str:
    """Double every backslash that does not already start a valid JSON escape."""
    return _JSON_ESCAPE_RE.sub(lambda m: m.group(0) if m.group(1) else "\\\\" + m.group(2), text)


def _introduces_control_characters(guidelines: list[Guideline], raw: str) -> bool:
    """Whether decoding produced C0 control characters the raw response never contained.

    The escape repair only runs on a response the model failed to escape properly, so its
    ``\\t``/``\\n``/``\\r`` almost certainly meant a literal backslash — a Windows path or a
    regex — rather than a control character. Decoding them anyway yields a guideline that
    looks valid but whose text is silently wrong, which then gets embedded and served on.
    Detect that and reject, rather than reporting a successful recovery.
    """
    present_in_raw = {ch for ch in raw if ch in _C0_CONTROL}
    for guideline in guidelines:
        for value in (guideline.content, guideline.rationale, guideline.trigger, *guideline.implementation_steps):
            if any(ch in _C0_CONTROL and ch not in present_in_raw for ch in value):
                return True
    return False


def parse_guideline_response(clean_response: str, context: str) -> list[Guideline] | None:
    """Parse a guideline-generation LLM response, repairing the malformations models
    commonly emit, and log whichever repair was needed.

    Tries the response as-is first, then two repairs that compose, so a response with
    both problems is still recovered:

    1. Lone backslashes escaped — models emit LaTeX-style ``\\( \\)`` inside string
       values, which is not a valid JSON escape sequence and fails to parse at all.
    2. A bare top-level array wrapped under the ``guidelines`` key — a bare ``[...]``
       parses as valid JSON but doesn't match the schema, so it fails validation
       rather than parsing. Only reachable when the provider can't enforce a response
       schema; constrained decoding makes the shape impossible to get wrong.

    A repair that succeeds is logged at INFO, so a prompt or model that keeps producing
    off-contract output stays visible instead of being silently rescued.

    Args:
        clean_response: Response text, already passed through ``clean_llm_response``.
        context: Pipeline name for log messages, e.g. ``"consistency"``.

    Returns:
        The parsed guidelines, or None if no variant validated — in which case the
        failure has already been logged, so callers need only handle the empty case.
    """
    variants: list[tuple[str, str]] = [("", clean_response)]
    escaped = _escape_lone_backslashes(clean_response)
    if escaped != clean_response:
        variants.append(("escaped lone backslashes", escaped))

    # Report the failure the unrepaired response produced — the later errors are
    # artifacts of the repair attempts and say less about what the model actually did.
    first_error: Exception | None = None

    for parse_repair, text in variants:
        try:
            parsed = json.loads(text)
        except JSONDecodeError as e:
            first_error = first_error or e
            continue

        payloads: list[tuple[str, object]] = [("", parsed)]
        if isinstance(parsed, list):
            payloads.append(('wrapped a bare array under "guidelines"', {"guidelines": parsed}))

        for shape_repair, payload in payloads:
            try:
                guidelines = GuidelineGenerationResponse.model_validate(payload).guidelines
            except ValidationError as e:
                first_error = first_error or e
                continue
            repairs = [r for r in (parse_repair, shape_repair) if r]
            if parse_repair and _introduces_control_characters(guidelines, clean_response):
                # Fail closed: a plausible-looking guideline with silently corrupted text is
                # worse than none, and the operator can regenerate.
                logger.warning(
                    f"Discarding {context} guideline response: escaping lone backslashes made it parse, but decoding "
                    f"introduced control characters absent from the response, so its text would be corrupted "
                    f"(a Windows path or regex read as \\t/\\n/\\r). Response: {repr(clean_response[:500])}"
                )
                return None
            if repairs:
                logger.info(f"Recovered {context} guideline response after repair: {'; '.join(repairs)}.")
            return guidelines

    logger.warning(f"Failed to parse {context} guideline response: {first_error}. Response: {repr(clean_response[:500])}")
    return None


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
            tool_calls = message.get("tool_calls")
            if isinstance(content, str) and content.strip():
                agent_steps.append({"type": "reasoning", "content": content, "raw": message})

            # Extract function calls (Agents SDK / Responses API shape: content is a list
            # of function_call items)
            if isinstance(content, list):
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

            # Extract function calls (native Chat Completions / Phoenix shape: content is
            # null and the call list lives in tool_calls)
            elif tool_calls:
                for call in tool_calls:
                    func = call.get("function", {})
                    name = func.get("name", "unknown")
                    args_str = func.get("arguments", "")
                    function_calls.append(
                        {
                            "type": "function_call",
                            "name": name,
                            "arguments": args_str,
                            "call_id": call.get("id", "unknown_call"),
                            "raw": call,
                        }
                    )

                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        if not isinstance(args, dict):
                            raise TypeError("tool-call arguments must be a JSON object")
                        args_display = ", ".join(f"{k}={json.dumps(v)}" for k, v in args.items())
                        function_description = f"{name}({args_display})"
                    except (JSONDecodeError, TypeError):
                        function_description = f"{name}({args_str})"

                    agent_steps.append(
                        {
                            "type": "action",
                            "content": function_description,
                            "raw": call,
                        }
                    )
            # Any other shape (e.g. empty content and no tool_calls) contributes no steps.

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

    llm_messages = dispatch_llm_pre_call(
        [{"role": "user", "content": prompt}], purpose="guideline_generation", model=llm_settings.guidelines_model
    )
    if constrained_decoding_supported:
        litellm.enable_json_schema_validation = True
        raw = (
            completion(
                model=llm_settings.guidelines_model,
                messages=llm_messages,
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
                messages=llm_messages,
                custom_llm_provider=llm_settings.custom_llm_provider,
            )
            .choices[0]
            .message.content
        )
    clean_response = clean_llm_response(raw)

    if not clean_response:
        logger.warning(f"LLM returned empty response for guideline generation. Model: {llm_settings.guidelines_model}")
        return GuidelineGenerationResult(guidelines=[], task_description=task_description)
    guidelines = parse_guideline_response(clean_response, "standard")
    return GuidelineGenerationResult(guidelines=guidelines or [], task_description=task_description)


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
    is_groq = llm_settings.custom_llm_provider == "groq" or llm_settings.guidelines_model.startswith("groq/")
    supported_params = get_supported_openai_params(
        model=llm_settings.guidelines_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    supports_response_format = supported_params and "response_format" in supported_params
    response_schema_enabled = supports_response_schema(
        model=llm_settings.guidelines_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    constrained_decoding_supported = bool(not is_groq and supports_response_format and response_schema_enabled)

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
