"""Trajectory resampling: generate multiple LLM responses per step for consistency analysis."""

import logging

logger = logging.getLogger(__name__)
from altk_evolve.llm.guidelines.consistency_analyzer.inference_utils import get_response_sampling


def _finish_reason(choice) -> str | None:
    if isinstance(choice, dict):
        return choice.get("finish_reason")
    return getattr(choice, "finish_reason", None)


def _reasoning_content(choice) -> str:
    """Reasoning-model scratchpad, returned alongside `content` and billed against the
    same max_tokens. Only read to explain an empty sample in the log — never scored."""
    if isinstance(choice, dict):
        return (choice.get("message") or {}).get("reasoning_content") or ""
    return getattr(getattr(choice, "message", None), "reasoning_content", None) or ""


def _carries_no_decision(response) -> bool:
    """True when a sample records no decision at all: blank text, or no tool calls.

    NOT the same as "content is empty" — a successful tool-call response legitimately
    has `content == ""`, which is why the extraction below must consult tool_calls
    first and only fall through to content when there are none.
    """
    if isinstance(response, str):
        return not response.strip()
    return not response


def extract_raw_samples(choices: list) -> dict:
    """Extract raw samples from response choices, dropping any that record no decision.

    A reasoning model spends its max_tokens budget on `reasoning_content` before
    emitting any `content`, so a step whose budget runs out returns
    `finish_reason='length'` with `content=''` — a successful HTTP response carrying
    nothing. Kept as samples, k of those are k *identical* empties, which scores as
    consistency 1.0 / uncertainty 0.0: a step the model could not answer would be
    read as one it is perfectly confident about, and `skip_on_no_uncertainty` would
    then suppress guidelines for the whole trajectory.

    Dropping them instead lets a step left with no samples fall through
    check_sample_validity to consistency -1 ("undefined"), which the score card
    excludes — an honest "we don't know" rather than a confident wrong answer.
    """
    response_list = []
    dropped = []

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

        if _carries_no_decision(response):
            dropped.append(choice)
            continue

        response_list.append(response)

    if dropped:
        truncated = sum(1 for c in dropped if _finish_reason(c) == "length")
        reasoned = sum(1 for c in dropped if _reasoning_content(c).strip())
        logger.warning(
            f"Discarded {len(dropped)} of {len(choices)} samples that recorded no decision "
            f"({truncated} truncated with finish_reason='length'"
            + (f", {reasoned} having spent the token budget on reasoning_content" if reasoned else "")
            + f"); scoring the {len(response_list)} that remain. A step left with none scores as "
            "consistency undefined rather than as perfectly consistent."
        )

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
        # Prefer the model actually used at this step in the original trajectory;
        # fall back to the configured default only when the step genuinely has none.
        model = step_model or model_name
        # custom_llm_provider is never recorded per-step in the trajectory (only
        # `model` is) — it's always a deployment-wide routing setting, so it applies
        # regardless of which model name is used for this step.
        tools = step.get("tools", None)

        response_samples = get_response_sampling(
            prompt=prompt,
            model_id=model,
            temperature=temperature,
            samples=samples,
            tools=tools,
            custom_llm_provider=custom_llm_provider,
        )

        step["sampling"] = extract_raw_samples(response_samples)

    return trajectory
