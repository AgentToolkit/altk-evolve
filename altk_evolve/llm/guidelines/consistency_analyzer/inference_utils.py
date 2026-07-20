"""
LLM inference utilities for the consistency analyzer.

Replaces the original IBM-specific provider dispatch (RITS, WatsonX, IBM LiteLLM)
with a single litellm.completion() call, matching the pattern used throughout the
rest of altk-evolve (see altk_evolve/llm/guidelines/guidelines.py).
"""

import logging

from litellm import completion

from altk_evolve.hooks.manager import dispatch_llm_pre_call
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
    custom_llm_provider: str | None = None,
) -> list:
    """Get multiple sampled responses via litellm.completion().

    Returns a list of Choice objects compatible with extract_raw_samples() in
    resampling.py (handles both the .message.tool_calls and .message.content paths).
    """
    messages = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
    # Dispatched once, OUTSIDE the retry loop below, so every retry re-sends the
    # same redacted messages rather than re-leaking the raw trajectory.
    llm_messages = dispatch_llm_pre_call(messages, purpose="consistency_resampling", model=model_id)
    kwargs: dict = dict(
        model=model_id,
        messages=llm_messages,
        temperature=temperature,
        max_tokens=max_token,
        n=samples,
    )
    if custom_llm_provider:
        kwargs["custom_llm_provider"] = custom_llm_provider
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
            if len(choices) < 2:
                # Provider ignored n>1 — retrying won't help; raise so the caller can
                # surface a real error rather than silently producing zero guidelines.
                raise EvolveException(
                    f"Requested n={samples} samples from {model_id} but got {len(choices)}. "
                    "Provider does not support n>1; consistency scoring requires at least 2 samples."
                )
            if len(choices) != samples:
                logger.warning(
                    f"Requested n={samples} samples from {model_id} but got {len(choices)}. "
                    "Consistency scores will be based on fewer samples than configured."
                )
            return choices
        except EvolveException:
            raise
        except Exception as e:
            last_error = e
            logger.debug(f"Resampling attempt {attempt + 1}/3 failed for {model_id}: {e}")

    raise EvolveException(f"Resampling failed after 3 attempts for model {model_id}") from last_error
