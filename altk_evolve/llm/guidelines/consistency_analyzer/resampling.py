"""Trajectory resampling: generate multiple LLM responses per step for consistency analysis."""

import logging

logger = logging.getLogger(__name__)
from altk_evolve.llm.guidelines.consistency_analyzer.inference_utils import get_response_sampling


def extract_raw_samples(choices: list) -> dict:
    """Extract raw samples from response choices."""
    response_list = []

    for choice in choices:
        if isinstance(choice, dict):
            if "tool_calls" in choice["message"]:
                response = choice["message"]["tool_calls"]
            else:
                response = choice["message"]["content"]
        else:
            if choice.message.tool_calls:
                response = [tc.model_dump() for tc in choice.message.tool_calls]
            else:
                response = choice.message.content

        if response is None:
            continue

        response_list.append(response)

    return {"num_samples": len(response_list), "raw_samples": response_list}


def resample_trajectory(
    trajectory: dict,
    samples: int,
    model_name: str,
    temperature: float = 0.5,
    max_steps: int = -1,
    custom_llm_provider: str | None = None,
) -> dict:
    """
    Resample a trajectory by generating multiple responses for each step.

    Args:
        trajectory: Trajectory dict with steps
        samples: Number of samples to generate per step
        temperature: Sampling temperature
        model_name: Model name to use
        max_steps: Max number of steps to resample (-1 for all steps)

    Returns:
        Trajectory with sampling data added to each step
    """
    logger.info(f"+++ Resampling trajectory ({trajectory.get('name', '')})")
    steps = trajectory["steps"] if max_steps == -1 else trajectory["steps"][:max_steps]
    for j, step in enumerate(steps):
        if "sampling" in step:
            # already sampled; skip this step but continue processing later steps
            logger.debug("+++ Found samples - skipping step resampling")
            continue

        if "llm_params" not in step:
            logger.debug("Skipping step %s — no llm_params", step["name"])
            continue

        logger.info(f"+++ Resampling step: {step['name']} ({j + 1}/{len(trajectory['steps'])})")

        prompt = step["messages"]
        step_model = step["llm_params"].get("model")
        model = step_model or model_name
        # Only forward the configured provider when falling back to the configured
        # model. For per-step models from the traced trajectory, let litellm infer
        # the provider to avoid misrouting (e.g. a claude model to the openai endpoint).
        provider = None if step_model else custom_llm_provider
        tools = step.get("tools", None)

        response_samples = get_response_sampling(
            prompt=prompt,
            model_id=model,
            temperature=temperature,
            samples=samples,
            tools=tools,
            custom_llm_provider=provider,
        )

        step["sampling"] = extract_raw_samples(response_samples)

    return trajectory
