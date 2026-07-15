"""
LLM inference utilities for the consistency analyzer.

Replaces the original IBM-specific provider dispatch (RITS, WatsonX, IBM LiteLLM)
with a single litellm.completion() call, matching the pattern used throughout the
rest of altk-evolve (see altk_evolve/llm/guidelines/guidelines.py).
"""

import logging

from litellm import completion

from altk_evolve.schema.exceptions import EvolveException

logger = logging.getLogger(__name__)

MAX_NEW_TOKENS = 3000


def get_response_sampling(
    prompt,
    model_id: str,
    temperature: float,
    samples: int,
    max_token: int = MAX_NEW_TOKENS,
    stop=None,
    logprobs: bool = False,
    tools: list | None = None,
) -> list:
    """Get multiple sampled responses via litellm.completion().

    Returns a list of Choice objects compatible with extract_raw_samples() in
    resampling.py (handles both the .message.tool_calls and .message.content paths).
    """
    messages = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
    kwargs: dict = dict(
        model=model_id,
        messages=messages,
        temperature=temperature,
        max_tokens=max_token,
        n=samples,
        # Do not forward custom_llm_provider from llm_settings here: model_id comes
        # from the traced trajectory and may be from a different provider than the one
        # configured for guideline generation. Let litellm infer the provider from the
        # model name to avoid misrouting (e.g. sending a claude model to the openai endpoint).
    )
    if stop:
        kwargs["stop"] = stop
    if logprobs:
        kwargs["logprobs"] = logprobs
    if tools:
        kwargs["tools"] = tools

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = completion(**kwargs)
            choices = response.choices
            if len(choices) != samples:
                logger.warning(
                    f"Requested n={samples} samples from {model_id} but got {len(choices)}. "
                    "Provider may not support n>1 — consistency scores will be based on fewer samples."
                )
            return choices
        except Exception as e:
            last_error = e
            logger.debug(f"Resampling attempt {attempt + 1}/3 failed for {model_id}: {e}")

    raise EvolveException(f"Resampling failed after 3 attempts for model {model_id}") from last_error
