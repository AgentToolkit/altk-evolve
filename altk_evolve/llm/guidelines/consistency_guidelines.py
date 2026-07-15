import json
import logging
import os
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Optional

import litellm
import yaml
from jinja2 import Template
from litellm import completion, get_supported_openai_params, supports_response_schema
from pydantic import ValidationError

from altk_evolve.llm.guidelines.consistency_analyzer.consistency_analysis import analyze_consistency
from altk_evolve.llm.guidelines.consistency_analyzer.resampling import resample_trajectory

from altk_evolve.config.llm import llm_settings
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.schema.guidelines import (
    DEFAULT_TASK_DESCRIPTION,
    GuidelineGenerationResponse,
    GuidelineGenerationResult,
)
from altk_evolve.utils.utils import clean_llm_response

logger = logging.getLogger(__name__)

HIGH_UNCERTAINTY = 0.2
LOW_UNCERTAINTY = 0.1
SKIP_ON_NO_UNCERTAINTY = True

_CONSISTENCY_GUIDELINES_TEMPLATE = Template((Path(__file__).parent / "prompts/generate_consistency_guidelines.jinja2").read_text())


def _strip_orphaned_tool_messages(messages: list[dict]) -> list[dict]:
    """Remove tool-role messages that have no preceding assistant tool_calls entry.

    When the gen_ai instrumentation format is used, tool_calls are not captured
    on intermediate assistant messages — only the text content (often "None").
    Sending such sequences to the LLM API during resampling causes a 400 error.
    This strips the orphaned tool messages so resampling receives a valid prompt.
    """
    result = []
    last_had_tool_calls = False
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            last_had_tool_calls = bool(msg.get("tool_calls"))
            result.append(msg)
        elif role == "tool":
            if last_had_tool_calls:
                result.append(msg)
            # else: skip — no tool_calls on the preceding assistant message
        else:
            result.append(msg)
    return result


def _is_well_formed_tool_calls(tool_calls: Any) -> bool:
    """Whether `tool_calls` is a clean OpenAI-format list (every entry names a function)."""
    return (
        isinstance(tool_calls, list)
        and bool(tool_calls)
        and all(isinstance(tc, dict) and isinstance(tc.get("function"), dict) and tc["function"].get("name") for tc in tool_calls)
    )


def _classify_step_response(msg: dict) -> tuple[str, Any]:
    """Classify an assistant message's raw_response_type: content, tool_calls, or other.

    "other" covers turns that don't cleanly fit either bucket — malformed tool_calls, or
    no usable text content and no tool_calls at all. Genuine OpenAI-protocol responses
    (native chat-completions output) should never actually land here; it exists as a safety
    net for less predictable/non-native response shapes.
    """
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        if _is_well_formed_tool_calls(tool_calls):
            return "tool_calls", json.dumps(tool_calls, indent=2)
        return "other", json.dumps(tool_calls, indent=2)

    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return "content", content

    return "other", content if content is not None else ""


def transform_trajectory_to_IR(trajectory: dict) -> dict:
    """Transform a trajectory into Intermediate Representation for the consistency analyzer.

    Produces a task + list-of-steps structure where each step carries the messages
    that preceded it, the assistant's raw response, and the LLM params used.

    Steps are named with an "OpenAIAgent" prefix when the trajectory carries a real OpenAI
    tools JSON schema (`trajectory["tools"]` populated) — meaning its tool_calls came from
    the native chat-completions protocol and can be resampled with real tool-calling rebound.
    Trajectories without one (e.g. smolagents' CodeAgent, which never declares an OpenAI
    `tools` param — it only describes tools in its system prompt) get a generic "AnyAgent"
    prefix instead, since we can't assume the same resampling behavior is safe for them.
    """
    messages = trajectory.get("messages", [])
    model = trajectory.get("model") or None
    tools = trajectory.get("tools")
    trace_id = trajectory.get("trace_id", "unknown")
    step_name = "OpenAIAgent" if tools else "AnyAgent"

    task = "Unknown task"
    for msg in messages:
        if msg.get("role") == "user":
            task = msg.get("content", "Unknown task")
            break

    steps: list[dict] = []
    step_number = 1
    current_messages: list[dict] = []

    for msg in messages:
        role = msg.get("role")

        if role == "assistant":
            raw_response_type, raw_response = _classify_step_response(msg)

            step = {
                "name": f"{step_name}_{raw_response_type}",
                "step_number": step_number,
                "raw_response": raw_response,
                "raw_response_type": raw_response_type,
                "messages": _strip_orphaned_tool_messages(current_messages.copy()),
                "llm_params": {"model": model},
            }
            if raw_response_type == "tool_calls":
                step["tools"] = tools

            steps.append(step)
            step_number += 1

        current_messages.append(msg)

    return {
        "task": task,
        "name": f"Trajectory {str(trace_id)[:8]}",
        "steps": steps,
    }


def _can_segment_trajectory(messages: list[dict]) -> bool:
    """True iff segment_trajectory step indices map 1:1 to IR step numbers.

    Safe when every assistant message has either:
    - a non-empty string content field, or
    - a list content field with exactly one function_call item.

    Falls back to False for chat completions format (tool_calls key present, content null)
    and for Agents SDK messages with multiple parallel function_calls in one message,
    both of which produce a step count mismatch between parse_openai_agents_trajectory
    and transform_trajectory_to_IR.
    """
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        if msg.get("tool_calls"):
            return False
        content = msg.get("content")
        if isinstance(content, str):
            if not content.strip():
                return False
        elif isinstance(content, list):
            fc_count = sum(1 for item in content if item.get("type") == "function_call")
            if fc_count != 1:
                return False
        else:
            return False
    return True


def parse_consistency_score_card(score_card: dict) -> dict:
    step_uncertainties: dict[int, float] = {}
    for step in score_card.get("steps", []):
        step_num = step.get("step_number")
        uncertainty = step.get("step_uncertainty")
        if step_num is not None and uncertainty is not None:
            step_uncertainties[step_num] = uncertainty

    return {
        "task": score_card.get("task"),
        "consistency_steps": score_card.get("consistency_steps"),
        "aggregate_trajectory_uncertainty": score_card.get("aggregate_trajectory_uncertainty"),
        "step_uncertainties": step_uncertainties,
    }


def format_trajectory_data(
    messages: list,
    consistency_data: dict,
    step_range: tuple[int, int] | None = None,
) -> str:
    step_uncertainties = consistency_data.get("step_uncertainties", {})

    if step_range:
        start, end = step_range
        step_uncertainties = {k: v for k, v in step_uncertainties.items() if start <= k <= end}

    TOP_N = 3
    top_steps = sorted(step_uncertainties.items(), key=lambda x: x[1], reverse=True)[:TOP_N]
    high_uncertainty_steps = {step_num: score for step_num, score in top_steps if score >= HIGH_UNCERTAINTY}

    if not high_uncertainty_steps and step_uncertainties:
        highest = max(step_uncertainties.items(), key=lambda x: x[1])
        if highest[1] > LOW_UNCERTAINTY:
            high_uncertainty_steps = {highest[0]: highest[1]}

    MAX_STEPS = 50
    steps_text: list[str] = []
    step_num = 0
    for step in messages:
        if step.get("role") != "assistant":
            continue
        step_num += 1
        if step_num > MAX_STEPS:
            break

        if step_range:
            if step_num > step_range[1]:
                break
            if step_num < step_range[0]:
                continue

        if step.get("tool_calls"):
            step_type = "Agent tool calls"
            this_step_text = ""
            for call in step["tool_calls"]:
                if this_step_text:
                    this_step_text += "\n"
                try:
                    func = call["function"]
                    args_raw = func["arguments"]
                    args_dict = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    if isinstance(args_dict, dict):
                        args_string = ", ".join(f"{k}={repr(v)}" for k, v in args_dict.items())
                    else:
                        args_string = repr(args_dict)
                    this_step_text += f"- {func['name']}({args_string})"
                except (KeyError, json.JSONDecodeError, TypeError):
                    this_step_text += f"- {call.get('id', 'unknown_call')}"
        else:
            step_type = "Agent reasoning"
            content = step.get("content", "") or ""
            if len(content) > 500:
                content = content[:500] + "..."
            this_step_text = content

        uncertainty_marker = ""
        if step_num in high_uncertainty_steps:
            uncertainty_marker = f" [⚠️ HIGH UNCERTAINTY: {high_uncertainty_steps[step_num]}]"

        steps_text.append(f"Step {step_num}{uncertainty_marker} - {step_type}:\n{this_step_text}")

    return "\n".join(steps_text)


def _safe_write_debug(path: Path, data: Any) -> None:
    try:
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning(f"Debug write failed (path={path}): {e} — production path unaffected")


def _write_guidelines_debug(debug_dir: Path, trace_id: Any, results: list[GuidelineGenerationResult], suffix: str = "") -> None:
    data = [{"task_description": r.task_description, "guidelines": [g.model_dump() for g in r.guidelines]} for r in results]
    _safe_write_debug(debug_dir / f"guidelines_{str(trace_id)[:8]}{suffix}.json", data)


def _generate_guideline_result(
    messages: list[dict],
    consistency_data: dict,
    task_description: str,
    step_range: tuple[int, int] | None,
    constrained_decoding_supported: bool,
    debug_dir: Path | None,
    trace_id: Any,
    debug_suffix: str,
) -> GuidelineGenerationResult:
    """Generate a single GuidelineGenerationResult for one segment (or the full trajectory).

    Applies the SKIP_ON_NO_UNCERTAINTY check scoped to the given step_range, renders
    the prompt, calls the LLM, and parses the response. debug_suffix distinguishes
    per-segment artifacts (e.g. "_seg1") from full-trajectory artifacts ("").
    """
    if SKIP_ON_NO_UNCERTAINTY:
        step_uncertainties = consistency_data.get("step_uncertainties", {})
        if step_range:
            start, end = step_range
            step_uncertainties = {k: v for k, v in step_uncertainties.items() if start <= k <= end}
        has_uncertain_steps = bool(step_uncertainties) and max(step_uncertainties.values()) > LOW_UNCERTAINTY
        if not has_uncertain_steps:
            logger.info(
                f"Skipping guideline generation{' for segment' + debug_suffix if debug_suffix else ''}: "
                f"no steps above LOW_UNCERTAINTY threshold ({LOW_UNCERTAINTY})"
            )
            return GuidelineGenerationResult(guidelines=[], task_description=task_description)

    trajectory_summary = format_trajectory_data(messages, consistency_data, step_range=step_range)

    prompt = _CONSISTENCY_GUIDELINES_TEMPLATE.render(
        task_instruction=task_description,
        trajectory_summary=trajectory_summary,
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
        logger.warning(f"LLM returned empty response for consistency guideline generation. Model: {llm_settings.guidelines_model}")
        return GuidelineGenerationResult(guidelines=[], task_description=task_description)

    try:
        guidelines = GuidelineGenerationResponse.model_validate(json.loads(clean_response)).guidelines
        return GuidelineGenerationResult(guidelines=guidelines, task_description=task_description)
    except JSONDecodeError:
        # LLMs sometimes emit LaTeX-style \( \) in string values which are not valid JSON
        # escape sequences. Escape lone backslashes and retry before giving up.
        fixed = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", clean_response)
        try:
            guidelines = GuidelineGenerationResponse.model_validate(json.loads(fixed)).guidelines
            return GuidelineGenerationResult(guidelines=guidelines, task_description=task_description)
        except (JSONDecodeError, ValidationError) as e:
            logger.warning(f"Failed to parse consistency guideline response: {e}. Response: {repr(clean_response[:500])}")
            return GuidelineGenerationResult(guidelines=[], task_description=task_description)
    except ValidationError as e:
        logger.warning(f"Failed to validate consistency guideline response: {e}. Response: {repr(clean_response[:500])}")
        return GuidelineGenerationResult(guidelines=[], task_description=task_description)


def generate_consistency_guidelines(
    trajectory: dict,
    config_path: Optional[Path | str] = None,
) -> list[GuidelineGenerationResult]:
    """Generate consistency-focused guidelines from an agent trajectory.

    Re-runs the trajectory N times via `consistency_analyzer.resampling`, scores per-step
    uncertainty, then asks an LLM to produce guidelines focused on the highest-uncertainty
    steps. When the trajectory format allows 1:1 step index mapping (non-empty string content
    or single-function_call list content per assistant message), the trajectory is first
    segmented into subtasks and guidelines are generated per subtask — matching the shape
    returned by `generate_guidelines` for downstream clustering. Falls back to full-trajectory
    generation when segmentation is not applicable or produces fewer than 2 valid subtasks.

    Returns a list with one GuidelineGenerationResult per subtask (or one for the full
    trajectory), matching the shape returned by `generate_guidelines`.

    Debug artifacts (IR, score card, prompt, guidelines JSON) are written when
    EVOLVE_DEBUG_DIR is set in the environment.

    Args:
        trajectory: dict with keys messages, model, trace_id, and optionally tools.
        config_path: YAML config consumed by consistency_analyzer. Defaults to
            `consistency_analyzer/agent_config.yaml`.
    """
    config_path = Path(config_path) if config_path else Path(__file__).parent / "consistency_analyzer" / "agent_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Consistency analyzer config not found: {config_path}. Provide config_path= or create the default file alongside this module."
        )
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    logger.info(f"Loaded consistency configuration from {config_path}")

    messages = trajectory.get("messages", [])
    model = trajectory.get("model")
    trace_id = trajectory.get("trace_id") or "unknown"

    if not messages:
        raise EvolveException("generate_consistency_guidelines called with empty messages")

    from altk_evolve.config.guidelines import guidelines_settings
    debug_dir = guidelines_settings.debug_dir
    if debug_dir:
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Could not create debug dir {debug_dir}: {e} — debug artifacts will be skipped")
            debug_dir = None
        else:
            _safe_write_debug(debug_dir / f"trajectory_{str(trace_id)[:8]}.json", trajectory)

    supported_params = get_supported_openai_params(
        model=llm_settings.guidelines_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    supports_response_format = supported_params and "response_format" in supported_params
    response_schema_enabled = supports_response_schema(
        model=llm_settings.guidelines_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    is_groq = llm_settings.custom_llm_provider == "groq" or llm_settings.guidelines_model.startswith("groq/")
    constrained_decoding_supported = bool(not is_groq and supports_response_format and response_schema_enabled)

    trajectory_ir = transform_trajectory_to_IR(trajectory)
    logger.info(f"Created trajectory IR for {trajectory_ir.get('name', '')}")

    steps = trajectory_ir.get("steps", [])
    n_steps = len(steps)
    if n_steps == 0:
        raise EvolveException("generate_consistency_guidelines called on trajectory with no steps")

    logger.info("Resampling trajectory IR")
    trajectory_ir = resample_trajectory(
        trajectory=trajectory_ir,
        samples=config.get("max_samples", 10),
        model_name=model or llm_settings.guidelines_model,
        max_steps=config.get("max_steps", -1),
    )

    logger.info(f"Computing consistency score card for {trajectory_ir.get('name', '')}")
    score_card, trajectory_ir = analyze_consistency(trajectory=trajectory_ir, config=config)
    if debug_dir:
        _safe_write_debug(debug_dir / f"trajectory_ir_{str(trace_id)[:8]}_cns.json", trajectory_ir)

    consistency_data = parse_consistency_score_card(score_card)
    if debug_dir:
        _safe_write_debug(debug_dir / f"consistency_score_card_{str(trace_id)[:8]}.json", score_card)

    task_description = trajectory_ir["task"] or DEFAULT_TASK_DESCRIPTION

    # --- Segmentation ---
    # Only attempt when every assistant message's content field allows a 1:1 step index
    # mapping between segment_trajectory and transform_trajectory_to_IR.
    subtasks = []
    if n_steps >= 2 and _can_segment_trajectory(messages):
        try:
            from altk_evolve.llm.guidelines.segmentation import segment_trajectory

            subtasks = segment_trajectory(messages)
        except Exception as e:
            logger.warning(f"Segmentation failed, falling back to full trajectory: {e}")
            subtasks = []

    valid_subtasks = []
    for subtask in subtasks:
        if 1 <= subtask.start_step <= subtask.end_step <= n_steps:
            valid_subtasks.append(subtask)
        else:
            logger.debug(f"Skipping subtask with out-of-range steps [{subtask.start_step}, {subtask.end_step}] (n_steps={n_steps})")

    if len(valid_subtasks) >= 2:
        logger.info(f"Segmented trajectory into {len(valid_subtasks)} subtasks")
        results = []
        for i, subtask in enumerate(valid_subtasks, 1):
            result = _generate_guideline_result(
                messages=messages,
                consistency_data=consistency_data,
                task_description=subtask.generalized_description,
                step_range=(subtask.start_step, subtask.end_step),
                constrained_decoding_supported=constrained_decoding_supported,
                debug_dir=debug_dir,
                trace_id=trace_id,
                debug_suffix=f"_seg{i}",
            )
            results.append(result)
        if debug_dir:
            _write_guidelines_debug(debug_dir, trace_id, results, "_consistency")
        return results

    # --- Full-trajectory fallback ---
    result = _generate_guideline_result(
        messages=messages,
        consistency_data=consistency_data,
        task_description=task_description,
        step_range=None,
        constrained_decoding_supported=constrained_decoding_supported,
        debug_dir=debug_dir,
        trace_id=trace_id,
        debug_suffix="",
    )
    if debug_dir:
        _write_guidelines_debug(debug_dir, trace_id, [result], "_consistency")
    return [result]
