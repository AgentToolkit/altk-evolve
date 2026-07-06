import json
import logging
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
    model = trajectory.get("model", "unknown")
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


def _write_guidelines_debug(debug_dir: Path, trace_id: Any, results: list[GuidelineGenerationResult]) -> None:
    payload = [
        {
            "task_description": result.task_description,
            "guidelines": [guideline.model_dump() for guideline in result.guidelines],
        }
        for result in results
    ]
    (debug_dir / f"guidelines_{str(trace_id)[:8]}.json").write_text(json.dumps(payload, indent=2))


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


def format_trajectory_data(messages: list, consistency_data: dict) -> str:
    step_uncertainties = consistency_data.get("step_uncertainties", {})
    TOP_N = 3
    top_steps = sorted(step_uncertainties.items(), key=lambda x: x[1], reverse=True)[:TOP_N]
    high_uncertainty_steps = {step_num: score for step_num, score in top_steps if score >= HIGH_UNCERTAINTY}

    if not high_uncertainty_steps and step_uncertainties:
        highest = max(step_uncertainties.items(), key=lambda x: x[1])
        if highest[1] > LOW_UNCERTAINTY:
            high_uncertainty_steps = {highest[0]: highest[1]}

    highest_step_num = max(high_uncertainty_steps.keys()) if high_uncertainty_steps else 0
    max_steps = max(highest_step_num + 2, 15)

    steps_text: list[str] = []
    step_num = 0
    for step in messages[:max_steps]:
        if step.get("role") != "assistant":
            continue
        step_num += 1

        if "tool_calls" in step:
            step_type = "Agent tool calls"
            this_step_text = ""
            for call in step["tool_calls"]:
                if this_step_text:
                    this_step_text += "\n"
                args_raw = call["function"]["arguments"]
                args_dict = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                args_string = ", ".join(f"{k}={repr(v)}" for k, v in args_dict.items())
                this_step_text += f"- {call['function']['name']}({args_string})"
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


def generate_consistency_guidelines(
    trajectory: dict,
    config_path: Optional[Path | str] = None,
    debug_output_dir: Optional[Path | str] = None,
) -> list[GuidelineGenerationResult]:
    """Generate consistency-focused guidelines from an agent trajectory.

    Re-runs the trajectory N times via `consistency_analyzer.resampling`, scores per-step
    uncertainty, then asks an LLM to produce guidelines focused on the highest-uncertainty
    steps. Returns `[GuidelineGenerationResult]` (single-element list) to match the shape
    returned by `generate_guidelines`.

    Args:
        trajectory: dict with keys messages, model, trace_id, and optionally tools.
        config_path: YAML config consumed by consistency_analyzer. Defaults to
            `agent_config.yaml` next to this module.
        debug_output_dir: if set, writes IR + score card JSON artifacts here for inspection.
    """
    config_path = Path(config_path) if config_path else Path(__file__).parent / "agent_config.yaml"
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

    debug_dir = Path(debug_output_dir) if debug_output_dir else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"trajectory_{str(trace_id)[:8]}.json").write_text(json.dumps(trajectory, indent=2))

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

    trajectory_ir = transform_trajectory_to_IR(trajectory)
    logger.info(f"Created trajectory IR for {trajectory_ir.get('name', '')}")
    if debug_dir:
        (debug_dir / f"trajectory_ir_{str(trace_id)[:8]}.json").write_text(json.dumps(trajectory_ir, indent=2))

    steps = trajectory_ir.get("steps", [])
    if len(steps) == 0:
        raise EvolveException("generate_consistency_guidelines called on trajectory with no steps")

    logger.info("Resampling trajectory IR")
    trajectory_ir = resample_trajectory(
        trajectory=trajectory_ir,
        samples=config.get("max_samples", 10),
        model_name=model or llm_settings.guidelines_model,
        max_steps=config.get("max_steps", -1),
    )
    if debug_dir:
        (debug_dir / f"trajectory_ir_{str(trace_id)[:8]}_spl.json").write_text(json.dumps(trajectory_ir, indent=2))

    logger.info(f"Computing consistency score card for {trajectory_ir.get('name', '')}")
    score_card, trajectory_ir = analyze_consistency(trajectory=trajectory_ir, config=config)
    if debug_dir:
        (debug_dir / f"trajectory_ir_{str(trace_id)[:8]}_cns.json").write_text(json.dumps(trajectory_ir, indent=2))

    consistency_data = parse_consistency_score_card(score_card)
    if debug_dir:
        (debug_dir / f"consistency_score_card_{str(trace_id)[:8]}.json").write_text(json.dumps(score_card, indent=2))

    if SKIP_ON_NO_UNCERTAINTY:
        step_uncertainties = consistency_data.get("step_uncertainties", {})
        has_uncertain_steps = bool(step_uncertainties) and max(step_uncertainties.values()) > LOW_UNCERTAINTY
        if not has_uncertain_steps:
            task_description = trajectory_ir["task"] or DEFAULT_TASK_DESCRIPTION
            logger.info(f"Skipping guideline generation: no steps above LOW_UNCERTAINTY threshold ({LOW_UNCERTAINTY})")
            results = [GuidelineGenerationResult(guidelines=[], task_description=task_description)]
            if debug_dir:
                _write_guidelines_debug(debug_dir, trace_id, results)
            return results

    trajectory_summary = format_trajectory_data(messages, consistency_data)
    task_description = trajectory_ir["task"] or DEFAULT_TASK_DESCRIPTION

    uncertainty = score_card.get("aggregate_trajectory_uncertainty", -1.0)
    if uncertainty > HIGH_UNCERTAINTY:
        success_probability = "LOW"
    elif 0 <= uncertainty < LOW_UNCERTAINTY:
        success_probability = "HIGH"
    else:
        success_probability = "MEDIUM"

    prompt_file = Path(__file__).parent / "prompts/generate_consistency_guidelines.jinja2"
    prompt = Template(prompt_file.read_text()).render(
        task_instruction=task_description,
        trajectory_summary=trajectory_summary,
        success_probability=success_probability,
        constrained_decoding_supported=constrained_decoding_supported,
    )

    if constrained_decoding_supported:
        litellm.enable_json_schema_validation = True
        clean_response = (
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
        results = [GuidelineGenerationResult(guidelines=[], task_description=task_description)]
        if debug_dir:
            _write_guidelines_debug(debug_dir, trace_id, results)
        return results

    try:
        guidelines = GuidelineGenerationResponse.model_validate(json.loads(clean_response)).guidelines
        results = [GuidelineGenerationResult(guidelines=guidelines, task_description=task_description)]
    except JSONDecodeError:
        # LLMs sometimes emit LaTeX-style \( \) in string values which are not valid JSON
        # escape sequences. Escape lone backslashes and retry before giving up.
        fixed = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", clean_response)
        try:
            guidelines = GuidelineGenerationResponse.model_validate(json.loads(fixed)).guidelines
            results = [GuidelineGenerationResult(guidelines=guidelines, task_description=task_description)]
        except (JSONDecodeError, ValidationError) as e:
            logger.warning(f"Failed to parse consistency guideline response: {e}. Response: {repr(clean_response[:500])}")
            results = [GuidelineGenerationResult(guidelines=[], task_description=task_description)]
    except ValidationError as e:
        logger.warning(f"Failed to validate consistency guideline response: {e}. Response: {repr(clean_response[:500])}")
        results = [GuidelineGenerationResult(guidelines=[], task_description=task_description)]

    if debug_dir:
        _write_guidelines_debug(debug_dir, trace_id, results)
    return results
