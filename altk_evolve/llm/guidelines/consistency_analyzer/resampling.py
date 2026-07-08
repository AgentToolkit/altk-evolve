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
            # don't sample again if there are already samples for this step
            logger.debug("+++ Found samples - skipping trajectory resampling")
            return trajectory

        if "llm_params" not in step:
            logger.debug("Skipping step %s — no llm_params", step["name"])
            continue

        logger.info(f"+++ Resampling step: {step['name']} ({j + 1}/{len(trajectory['steps'])})")

        if "llm_params" in step:
            prompt = step["messages"]
            model = step["llm_params"].get("model") or model_name
            tools = step.get("tools", None)

            response_samples = get_response_sampling(
                prompt=prompt,
                model_id=model,
                temperature=temperature,
                samples=samples,
                tools=tools,
            )
        else:
            # this is the case when we are processing old format CUGA trajectories
            prompt_item = step["prompts"][0]
            if prompt_item["role"] != "system":
                logger.debug(f"+++ Cannot resample step {step['name']} - skipping")
                continue
            prompt = prompt_item["value"]
            # check whether we are dealing with the newer CUGA format
            human_prompt_item = step["prompts"][1]
            if human_prompt_item["role"] == "human":
                prompt = prompt + "\nHuman: " + human_prompt_item["value"]
                # logger.debug(f"Prompt:\n{prompt[:(len(prompt_item["value"])+100)]}")

            response_samples = get_response_sampling(
                prompt=prompt,
                model_id=model_name,
                temperature=temperature,
                samples=samples,
            )

        step["sampling"] = extract_raw_samples(response_samples)

    return trajectory
